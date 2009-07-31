#!/usr/bin/env python

# GUI for TextTest written with PyGTK
# First make sure we can import the GUI modules: if we can't, throw appropriate exceptions

import texttest_version

def raiseException(msg):
    from plugins import TextTestError
    raise TextTestError, "Could not start TextTest " + texttest_version.version + " GUI due to PyGTK GUI library problems :\n" + msg

try:
    import gtk
except Exception, e:
    raiseException("Unable to import module 'gtk' - " + str(e))

pygtkVersion = gtk.pygtk_version
requiredPygtkVersion = texttest_version.required_pygtk_version
if pygtkVersion < requiredPygtkVersion:
    raiseException("TextTest " + texttest_version.version + " GUI requires at least PyGTK " +
                   ".".join(map(lambda l: str(l), requiredPygtkVersion)) + ": found version " +
                   ".".join(map(lambda l: str(l), pygtkVersion)))

try:
    import gobject
except:
    raiseException("Unable to import module 'gobject'")

import gtkusecase, pango, testtree, filetrees, guiplugins, plugins, os, sys, subprocess, logging
from ndict import seqdict
from copy import copy


#
# A class responsible for putting messages in the status bar.
# It is also responsible for keeping the throbber rotating
# while actions are under way.
#
class GUIStatusMonitor(guiplugins.SubGUI):
    def __init__(self):
        guiplugins.SubGUI.__init__(self)
        self.throbber = None
        self.animation = None
        self.pixbuf = None
        self.label = None

    def getWidgetName(self):
        return "_Status bar"

    def notifyActionStart(self, message="", lock = True):
        if self.throbber:
            if self.pixbuf: # pragma: no cover : Only occurs if some code forgot to do ActionStop ...
                self.notifyActionStop()
            self.pixbuf = self.throbber.get_pixbuf()
            self.throbber.set_from_animation(self.animation)
            if lock:
                self.throbber.grab_add()

    def notifyActionProgress(self, message=""):
        while gtk.events_pending():
            gtk.main_iteration(False)

    def notifyActionStop(self, message=""):
        if self.throbber:
            self.throbber.set_from_pixbuf(self.pixbuf)
            self.pixbuf = None
            self.throbber.grab_remove()

    def notifyStatus(self, message):
        if self.label:
            self.label.set_markup(plugins.convertForMarkup(message))

    def createView(self):
        hbox = gtk.HBox()
        self.label = gtk.Label()
        self.label.set_name("GUI status")
        self.label.set_ellipsize(pango.ELLIPSIZE_END)
        # It seems difficult to say 'ellipsize when you'd otherwise need
        # to enlarge the window', so we'll have to settle for a fixed number
        # of max char's ... The current setting (90) is just a good choice
        # based on my preferred window size, on the test case I used to
        # develop this code. (since different chars have different widths,
        # the optimal number depends on the string to display) \ Mattias++
        self.label.set_max_width_chars(90)
        self.label.set_use_markup(True)
        self.label.set_markup(plugins.convertForMarkup("TextTest started at " + plugins.localtime() + "."))
        hbox.pack_start(self.label, expand=False, fill=False)
        imageDir = plugins.installationDir("images")
        try:
            staticIcon = os.path.join(imageDir, "throbber_inactive.png")
            temp = gtk.gdk.pixbuf_new_from_file(staticIcon)
            self.throbber = gtk.Image()
            self.throbber.set_from_pixbuf(temp)
            animationIcon = os.path.join(imageDir, "throbber_active.gif")
            self.animation = gtk.gdk.PixbufAnimation(animationIcon)
            hbox.pack_end(self.throbber, expand=False, fill=False)
        except Exception, e:
            plugins.printWarning("Failed to create icons for the status throbber:\n" + str(e) + "\nAs a result, the throbber will be disabled.")
            self.throbber = None
        self.widget = gtk.Frame()
        self.widget.set_shadow_type(gtk.SHADOW_ETCHED_IN)
        self.widget.add(hbox)
        self.widget.show_all()
        return self.widget

# To make it easier for all sorts of things to connect
# to the status bar, let it be global, at least for now ...
statusMonitor = GUIStatusMonitor()

class IdleHandlerManager:
    def __init__(self):
        self.sourceId = -1
        self.diag = logging.getLogger("Idle Handlers")
    def notifyActionStart(self, message="", lock=True):
        # To make it possible to have an while-events-process loop
        # to update the GUI during actions, we need to make sure the idle
        # process isn't run. We hence remove that for a while here ...
        if lock:
            self.disableHandler()
            scriptEngine.replayer.disableIdleHandlers()

    def shouldShow(self):
        return True # nothing to show, but we need to observe...

    def notifyActionProgress(self, *args):
        if self.sourceId >= 0:
            raise plugins.TextTestError, "No Action currently exists to have progress on!"

    def notifyActionStop(self, *args):
        # Activate idle function again, see comment in notifyActionStart
        self.enableHandler()
        scriptEngine.replayer.reenableIdleHandlers()

    def addSuites(self, *args):
        self.enableHandler()

    def enableHandler(self):
        if self.sourceId == -1:
            # Same priority as PyUseCase replay, so they get called interchangeably
            # Non-default as a workaround for bugs in filechooser handling in GTK
            self.sourceId = plugins.Observable.threadedNotificationHandler.enablePoll(gobject.idle_add,
                                                                                      priority=gtkusecase.PRIORITY_PYUSECASE_IDLE)
            self.diag.info("Adding idle handler")

    def disableHandler(self):
        if self.sourceId >= 0:
            self.diag.info("Removing idle handler")
            gobject.source_remove(self.sourceId)
            self.sourceId = -1

    def notifyAllComplete(self):
        self.diag.info("Disabling thread-based polling")
        plugins.Observable.threadedNotificationHandler.disablePoll()
    def notifyExit(self):
        self.disableHandler()


class TextTestGUI(plugins.Responder, plugins.Observable):
    scriptEngine = None
    def __init__(self, optionMap, allApps):
        vanilla = optionMap.has_key("vanilla")
        self.readGtkRCFiles(vanilla)
        self.dynamic = not optionMap.has_key("gx")
        self.setUpGlobals(allApps)
        plugins.Responder.__init__(self)
        plugins.Observable.__init__(self)
        testCount = int(optionMap.get("count", 0))

        self.appFileGUI = filetrees.ApplicationFileGUI(self.dynamic, allApps)
        self.textInfoGUI = TextInfoGUI()
        self.runInfoGUI = RunInfoGUI(self.dynamic)
        self.testRunInfoGUI = TestRunInfoGUI(self.dynamic)
        self.progressMonitor = TestProgressMonitor(self.dynamic, testCount)
        self.progressBarGUI = ProgressBarGUI(self.dynamic, testCount)
        self.idleManager = IdleHandlerManager()
        uiManager = gtk.UIManager()
        self.defaultActionGUIs, self.actionTabGUIs = \
                                guiplugins.interactiveActionHandler.getPluginGUIs(self.dynamic, allApps, uiManager)
        self.menuBarGUI, self.toolBarGUI, testPopupGUI, testFilePopupGUI = self.createMenuAndToolBarGUIs(allApps, vanilla, uiManager)
        self.testColumnGUI = testtree.TestColumnGUI(self.dynamic, testCount)
        self.testTreeGUI = testtree.TestTreeGUI(self.dynamic, allApps, testPopupGUI, self.testColumnGUI)
        self.testFileGUI = filetrees.TestFileGUI(self.dynamic, testFilePopupGUI)
        self.rightWindowGUI = self.createRightWindowGUI()
        self.shortcutBarGUI = ShortcutBarGUI()
        self.topWindowGUI = self.createTopWindowGUI(allApps)

    def setUpGlobals(self, allApps):
        global guilog, guiConfig, scriptEngine
        scriptEngine = self.scriptEngine
        guilog = logging.getLogger("gui log")
        guiConfig = guiplugins.GUIConfig(self.dynamic, allApps, guilog)

        guiplugins.guilog = guilog
        guiplugins.scriptEngine = scriptEngine
        guiplugins.guiConfig = guiConfig

    def getTestTreeObservers(self):
        return [ self.testColumnGUI, self.testFileGUI, self.textInfoGUI, self.testRunInfoGUI ] + self.allActionGUIs() + [ self.rightWindowGUI ]
    def allActionGUIs(self):
        return self.defaultActionGUIs + self.actionTabGUIs
    def getLifecycleObservers(self):
        # only the things that want to know about lifecycle changes irrespective of what's selected,
        # otherwise we go via the test tree. Include add/remove as lifecycle, also final completion
        return [ self.progressBarGUI, self.progressMonitor, self.testTreeGUI,
                 statusMonitor, self.runInfoGUI, self.idleManager, self.topWindowGUI ]
    def getActionObservers(self):
        return [ self.testTreeGUI, self.testFileGUI, statusMonitor, self.runInfoGUI, self.idleManager, self.topWindowGUI ]

    def getFileViewObservers(self):
        observers = self.defaultActionGUIs + self.actionTabGUIs
        if self.dynamic:
            observers.append(self.textInfoGUI)
        return observers
    
    def isFrameworkExitObserver(self, obs):
        return hasattr(obs, "notifyExit") or hasattr(obs, "notifyKillProcesses")
    def getExitObservers(self, frameworkObservers):
        # Don't put ourselves in the observers twice or lots of weird stuff happens.
        # Important that closing the GUI is the last thing to be done, so make sure we go at the end...
        frameworkExitObservers = filter(self.isFrameworkExitObserver, frameworkObservers)
        return self.defaultActionGUIs + [ guiplugins.processMonitor, statusMonitor, self.testTreeGUI, self.menuBarGUI ] + \
               frameworkExitObservers + [ self.idleManager, self ]
    def getTestColumnObservers(self):
        return [ self.testTreeGUI, statusMonitor, self.idleManager ]
    def getHideableGUIs(self):
        return [ self.toolBarGUI, self.shortcutBarGUI, statusMonitor ]
    def getAddSuitesObservers(self):
        actionObservers = filter(lambda obs: hasattr(obs, "addSuites"), self.defaultActionGUIs + self.actionTabGUIs)
        return [ guiplugins.guiConfig, self.testColumnGUI, self.appFileGUI ] + actionObservers + \
               [ self.rightWindowGUI, self.topWindowGUI, self.idleManager ]
    def setObservers(self, frameworkObservers):
        # We don't actually have the framework observe changes here, this causes duplication. Just forward
        # them as appropriate to where they belong. This is a bit of a hack really.
        for observer in self.getTestTreeObservers():
            if observer.shouldShow():
                self.testTreeGUI.addObserver(observer)

        for observer in self.getTestColumnObservers():
            self.testColumnGUI.addObserver(observer)

        for observer in self.getFileViewObservers():
            self.testFileGUI.addObserver(observer)
            self.appFileGUI.addObserver(observer)

        # watch for category selections
        self.progressMonitor.addObserver(self.testTreeGUI)
        guiplugins.processMonitor.addObserver(statusMonitor)
        self.textInfoGUI.addObserver(statusMonitor)
        for observer in self.getLifecycleObservers():
            if observer.shouldShow():
                self.addObserver(observer) # forwarding of test observer mechanism

        actionGUIs = self.allActionGUIs()
        # mustn't send ourselves here otherwise signals get duplicated...
        frameworkObserversToUse = filter(lambda obs: obs is not self, frameworkObservers)
        observers = actionGUIs + self.getActionObservers() + frameworkObserversToUse
        for actionGUI in actionGUIs:
            actionGUI.setObservers(observers)

        for observer in self.getHideableGUIs():
            self.menuBarGUI.addObserver(observer)

        for observer in self.getExitObservers(frameworkObserversToUse):
            self.topWindowGUI.addObserver(observer)

    def readGtkRCFiles(self, vanilla):
        for file in plugins.findDataPaths([ ".gtkrc-2.0" ], vanilla, includePersonal=True):
            gtk.rc_add_default_file(file)

    def addSuites(self, suites):
        for observer in self.getAddSuitesObservers():
            observer.addSuites(suites)

    def shouldShrinkMainPanes(self):
        # If we maximise there is no point in banning pane shrinking: there is nothing to gain anyway and
        # it doesn't seem to work very well :)
        return not self.dynamic or guiConfig.getWindowOption("maximize")

    def createTopWindowGUI(self, allApps):
        mainWindowGUI = PaneGUI(self.testTreeGUI, self.rightWindowGUI, horizontal=True, shrink=self.shouldShrinkMainPanes())
        parts = [ self.menuBarGUI, self.toolBarGUI, mainWindowGUI, self.shortcutBarGUI, statusMonitor ]
        boxGUI = VBoxGUI(parts)
        return TopWindowGUI(boxGUI, self.dynamic, allApps)

    def createMenuAndToolBarGUIs(self, allApps, vanilla, uiManager):
        menu = MenuBarGUI(allApps, self.dynamic, vanilla, uiManager, self.allActionGUIs())
        toolbar = ToolBarGUI(uiManager, self.progressBarGUI)
        testPopup = PopupMenuGUI("TestPopupMenu", uiManager)
        testFilePopup = PopupMenuGUI("TestFilePopupMenu", uiManager)
        return menu, toolbar, testPopup, testFilePopup

    def createRightWindowGUI(self):
        testTab = PaneGUI(self.testFileGUI, self.textInfoGUI, horizontal=False)
        runInfoTab = PaneGUI(self.runInfoGUI, self.testRunInfoGUI, horizontal=False)
        tabGUIs = [ self.appFileGUI, testTab, self.progressMonitor, runInfoTab ] + self.actionTabGUIs

        tabGUIs = filter(lambda tabGUI: tabGUI.shouldShow(), tabGUIs)
        subNotebookGUIs = self.createNotebookGUIs(tabGUIs)
        return ChangeableNotebookGUI(subNotebookGUIs, self.getNotebookScriptName("Top"))

    def getNotebookScriptName(self, tabName):
        if tabName == "Top":
            return "view options for"
        else:
            return "view sub-options for " + tabName.lower() + " :"

    def classifyByTitle(self, tabGUIs):
        return map(lambda tabGUI: (tabGUI.getTabTitle(), tabGUI), tabGUIs)
    def getGroupTabNames(self, tabGUIs):
        tabNames = [ "Test", "Selection", "Running" ]
        for tabGUI in tabGUIs:
            tabName = tabGUI.getGroupTabTitle()
            if not tabName in tabNames:
                tabNames.append(tabName)
        return tabNames
    def createNotebookGUIs(self, tabGUIs):
        tabInfo = []
        for tabName in self.getGroupTabNames(tabGUIs):
            currTabGUIs = filter(lambda tabGUI: tabGUI.getGroupTabTitle() == tabName, tabGUIs)
            if len(currTabGUIs) > 1:
                notebookGUI = NotebookGUI(self.classifyByTitle(currTabGUIs), self.getNotebookScriptName(tabName))
                tabInfo.append((tabName, notebookGUI))
            elif len(currTabGUIs) == 1:
                tabInfo.append((tabName, currTabGUIs[0]))
        return tabInfo
    def run(self):
        gtk.main()
    def notifyExit(self):
        gtk.main_quit()
    def notifyLifecycleChange(self, test, state, changeDesc):
        test.stateInGui = state
        self.notify("LifecycleChange", test, state, changeDesc)
    def notifyDescriptionChange(self, test):
        self.notify("DescriptionChange", test)
    def notifyFileChange(self, test):
        self.notify("FileChange", test)
    def notifyContentChange(self, *args, **kwargs):
        self.notify("ContentChange", *args, **kwargs)
    def notifyNameChange(self, *args, **kwargs):
        self.notify("NameChange", *args, **kwargs)
    def notifyStartRead(self):
        if not self.dynamic:
            self.notify("Status", "Reading tests ...")
            self.notify("ActionStart", "", False)
    def notifyAllRead(self, suites):
        if not self.dynamic:
            self.notify("Status", "Reading tests completed at " + plugins.localtime() + ".")
            self.notify("ActionStop")
        self.notify("AllRead", suites)
        if self.dynamic and len(suites) == 0:
            guilog.info("There weren't any tests to run, terminating...")
            self.topWindowGUI.forceQuit()

    def notifyAdd(self, test, *args, **kwargs):
        test.stateInGui = test.state
        self.notify("Add", test, *args, **kwargs)
    def notifyStatus(self, *args, **kwargs):
        self.notify("Status", *args, **kwargs)
    def notifyRemove(self, test):
        self.notify("Remove", test)
    def notifyAllComplete(self):
        self.notify("AllComplete")

class TopWindowGUI(guiplugins.ContainerGUI):
    EXIT_NOTIFIED = 1
    COMPLETION_NOTIFIED = 2
    def __init__(self, contentGUI, dynamic, allApps):
        guiplugins.ContainerGUI.__init__(self, [ contentGUI ])
        self.dynamic = dynamic
        self.topWindow = None
        self.allApps = copy(allApps)
        self.exitStatus = 0
        if not self.dynamic:
            self.exitStatus |= self.COMPLETION_NOTIFIED # no tests to wait for...

    def getCheckoutTitle(self):
        allCheckouts = []
        for app in self.allApps:
            checkout = app.checkout
            if checkout and not checkout in allCheckouts:
                allCheckouts.append(checkout)
        if len(allCheckouts) == 0:
            return ""
        elif len(allCheckouts) == 1:
            return " under " + allCheckouts[0]
        else:
            return " from various checkouts"

    def addSuites(self, suites):
        for suite in suites:
            if suite.app.fullName() not in [ app.fullName() for app in self.allApps ]:
                self.allApps.append(suite.app)
                self.setWindowTitle()
                
        if not self.topWindow:
            # only do this once, not when new suites are added...
            self.createView()
            
    def createView(self):
        # Create toplevel window to show it all.
        self.topWindow = gtk.Window(gtk.WINDOW_TOPLEVEL)
        try:
            import stockitems
            stockitems.register(self.topWindow)
        except: #pragma : no cover - should never happen
            plugins.printWarning("Failed to register texttest stock icons.")
            plugins.printException()
        self.topWindow.set_icon_from_file(self.getIcon())
        self.setWindowTitle()

        self.topWindow.add(self.subguis[0].createView())
        self.adjustSize()
        self.topWindow.show()
        self.topWindow.set_default_size(-1, -1)

        self.notify("TopWindow", self.topWindow)
        scriptEngine.connect("close window", "delete_event", self.topWindow, self.notifyQuit)
        return self.topWindow

    def setWindowTitle(self):
        allAppNames = [ repr(app) for app in self.allApps ]
        appNameDesc = ",".join(allAppNames)
        if self.dynamic:
            checkoutTitle = self.getCheckoutTitle()
            self.topWindow.set_title("TextTest dynamic GUI : testing " + appNameDesc + checkoutTitle + \
                                     " (started at " + plugins.startTimeString() + ")")
        else:
            if len(appNameDesc) > 0:
                appNameDesc = " for " + appNameDesc
            self.topWindow.set_title("TextTest static GUI : management of tests" + appNameDesc)

    def getIcon(self):
        imageDir = plugins.installationDir("images")
        if self.dynamic:
            return os.path.join(imageDir, "texttest-icon-dynamic.jpg")
        else:
            return os.path.join(imageDir, "texttest-icon-static.jpg")

    def forceQuit(self):
        self.exitStatus |= self.COMPLETION_NOTIFIED
        self.notifyQuit()

    def notifyAllComplete(self, *args):
        self.exitStatus |= self.COMPLETION_NOTIFIED
        if self.exitStatus & self.EXIT_NOTIFIED:
            self.terminate()

    def notifyQuit(self, *args):
        self.exitStatus |= self.EXIT_NOTIFIED
        self.notify("KillProcesses")
        if self.exitStatus & self.COMPLETION_NOTIFIED:
            self.terminate()
        else:
            statusMonitor.notifyStatus("Waiting for all tests to terminate ...")
            # When they have, we'll get notifyAllComplete

    def notifyAnnotate(self, annotation):
        self.topWindow.set_title("TextTest dynamic GUI : " + annotation)
        guilog.info("Top Window title is " + self.topWindow.get_title())

    def terminate(self):
        self.notify("Exit")
        self.topWindow.destroy()

    def adjustSize(self):
        if guiConfig.getWindowOption("maximize"):
            self.topWindow.maximize()
            guilog.info("Maximising top window...")
        else:
            width, widthDescriptor = self.getWindowDimension("width")
            height, heightDescriptor  = self.getWindowDimension("height")
            self.topWindow.set_default_size(width, height)
            guilog.info(widthDescriptor)
            guilog.info(heightDescriptor)

    def getWindowDimension(self, dimensionName):
        pixelDimension = guiConfig.getWindowOption(dimensionName + "_pixels")
        if pixelDimension != "<not set>":
            descriptor = "Setting window " + dimensionName + " to " + pixelDimension + " pixels."
            return int(pixelDimension), descriptor
        else:
            fullSize = eval("gtk.gdk.screen_" + dimensionName + "()")
            proportion = float(guiConfig.getWindowOption(dimensionName + "_screen"))
            descriptor = "Setting window " + dimensionName + " to " + repr(int(100.0 * proportion)) + "% of screen."
            return int(fullSize * proportion), descriptor


class MenuBarGUI(guiplugins.SubGUI):
    def __init__(self, allApps, dynamic, vanilla, uiManager, actionGUIs):
        guiplugins.SubGUI.__init__(self)
        # Create GUI manager, and a few default action groups
        self.menuNames = guiplugins.interactiveActionHandler.getMenuNames(allApps)
        self.dynamic = dynamic
        self.vanilla = vanilla
        self.uiManager = uiManager
        self.actionGUIs = actionGUIs
        self.actionGroup = self.uiManager.get_action_groups()[0]
        self.toggleActions = []
        self.diag = logging.getLogger("Menu Bar")
    def shouldHide(self, name):
        return guiConfig.getCompositeValue("hide_gui_element", name, modeDependent=True)
    def toggleVisibility(self, action, observer, *args):
        widget = observer.widget
        oldVisible = widget.get_property('visible')
        newVisible = action.get_active()
        if oldVisible and not newVisible:
            widget.hide()
        elif newVisible and not oldVisible:
            widget.show()
    def createToggleActions(self):
        for observer in self.observers:
            actionTitle = observer.getWidgetName()
            actionName = actionTitle.replace("_", "")
            gtkAction = gtk.ToggleAction(actionName, actionTitle, None, None)
            gtkAction.set_active(True)
            self.actionGroup.add_action(gtkAction)
            gtkAction.connect("toggled", self.toggleVisibility, observer)
            scriptEngine.registerToggleButton(gtkAction, "show " + actionName, "hide " + actionName)
            self.toggleActions.append(gtkAction)
    def createView(self):
        # Initialize
        for menuName in self.menuNames:
            realMenuName = menuName
            if not menuName.isupper():
                realMenuName = menuName.capitalize()
            self.actionGroup.add_action(gtk.Action(menuName + "menu", "_" + realMenuName, None, None))
        self.createToggleActions()

        for file in self.getGUIDescriptionFileNames():
            try:
                self.diag.info("Reading UI from file " + file)
                self.uiManager.add_ui_from_file(file)
            except Exception, e:
                raise plugins.TextTestError, "Failed to parse GUI description file '" + file + "': " + str(e)
        self.uiManager.ensure_update()
        self.widget = self.uiManager.get_widget("/MainMenuBar")
        return self.widget
    
    def notifyTopWindow(self, window):
        window.add_accel_group(self.uiManager.get_accel_group())
        if self.shouldHide("menubar"):
            self.widget.hide()
        for toggleAction in self.toggleActions:
            if self.shouldHide(toggleAction.get_name()):
                toggleAction.set_active(False)

    def getGUIDescriptionFileNames(self):
        allFiles = plugins.findDataPaths([ "*.xml" ], self.vanilla, includePersonal=True)
        self.diag.info("All description files : " + repr(allFiles))
        # Pick up all GUI descriptions corresponding to modules we've loaded
        loadFiles = filter(self.shouldLoad, allFiles)
        loadFiles.sort(self.cmpDescFiles)
        return loadFiles

    def cmpDescFiles(self, file1, file2):
        base1 = os.path.basename(file1)
        base2 = os.path.basename(file2)
        default1 = base1.startswith("default")
        default2 = base2.startswith("default")
        if default1 != default2:
            return cmp(default2, default1)
        partCount1 = base1.count("-")
        partCount2 = base2.count("-")
        if partCount1 != partCount2:
            return cmp(partCount1, partCount2) # less - implies read first (not mode-specific)
        return cmp(base2, base1) # something deterministic, just to make sure it's the same for everyone
    def shouldLoad(self, fileName):
        baseName = os.path.basename(fileName)
        if (baseName.endswith("-dynamic.xml") and self.dynamic) or \
               (baseName.endswith("-static.xml") and not self.dynamic):
            moduleName = "-".join(baseName.split("-")[:-1])
        else:
            moduleName = baseName[:-4]
        self.diag.info("Checking if we loaded module " + moduleName)
        packageName = ".".join(__name__.split(".")[:-1])
        return sys.modules.has_key(moduleName) or sys.modules.has_key(packageName + "." + moduleName)
    

class ToolBarGUI(guiplugins.ContainerGUI):
    def __init__(self, uiManager, subgui):
        guiplugins.ContainerGUI.__init__(self, [ subgui ])
        self.uiManager = uiManager
    def getWidgetName(self):
        return "_Toolbar"
    def ensureVisible(self, toolbar):
        for item in toolbar.get_children():
            item.set_is_important(True) # Or newly added children without stock ids won't be visible in gtk.TOOLBAR_BOTH_HORIZ style
    def shouldShow(self):
        return True # don't care about whether we have a progress bar or not
    def createView(self):
        self.uiManager.ensure_update()
        toolbar = self.uiManager.get_widget("/MainToolBar")
        self.ensureVisible(toolbar)

        self.widget = gtk.HandleBox()
        self.widget.add(toolbar)
        toolbar.set_orientation(gtk.ORIENTATION_HORIZONTAL)
        progressBarGUI = self.subguis[0]
        if progressBarGUI.shouldShow():
            progressBar = progressBarGUI.createView()
            width = 7 # Looks good, same as gtk.Paned border width
            alignment = gtk.Alignment()
            alignment.set(1.0, 1.0, 1.0, 1.0)
            alignment.set_padding(width, width, 1, width)
            alignment.add(progressBar)
            toolItem = gtk.ToolItem()
            toolItem.add(alignment)
            toolItem.set_expand(True)
            toolbar.insert(toolItem, -1)

        self.widget.show_all()
        return self.widget


class PopupMenuGUI(guiplugins.SubGUI):
    def __init__(self, name, uiManager):
        guiplugins.SubGUI.__init__(self)
        self.name = name
        self.uiManager = uiManager
    def createView(self):
        self.uiManager.ensure_update()
        self.widget = self.uiManager.get_widget("/" + self.name)
        self.widget.show_all()
        return self.widget
    def showMenu(self, treeview, event):
        if event.button == 3 and len(self.widget.get_children()) > 0:
            time = event.time
            pathInfo = treeview.get_path_at_pos(int(event.x), int(event.y))
            selection = treeview.get_selection()
            selectedRows = selection.get_selected_rows()
            # If they didnt right click on a currently selected
            # row, change the selection
            if pathInfo is not None:
                if pathInfo[0] not in selectedRows[1]:
                    selection.unselect_all()
                    selection.select_path(pathInfo[0])
                path, col, cellx, celly = pathInfo
                treeview.grab_focus()
                self.widget.popup(None, None, None, event.button, time)
                return True


class ShortcutBarGUI(guiplugins.SubGUI):
    def getWidgetName(self):
        return "_Shortcut bar"
    def createView(self):
        self.widget = scriptEngine.createShortcutBar()
        self.widget.set_name(self.getWidgetName().replace("_", ""))
        self.widget.show()
        return self.widget
    


class VBoxGUI(guiplugins.ContainerGUI):    
    def createView(self):
        box = gtk.VBox()
        expandWidgets = [ gtk.HPaned, gtk.ScrolledWindow ]
        for subgui in self.subguis:
            view = subgui.createView()
            expand = view.__class__ in expandWidgets
            box.pack_start(view, expand=expand, fill=expand)

        box.show()
        return box


class NotebookGUI(guiplugins.SubGUI):
    def __init__(self, tabInfo, scriptTitle):
        guiplugins.SubGUI.__init__(self)
        self.scriptTitle = scriptTitle
        self.diag = logging.getLogger("GUI notebook")
        self.tabInfo = tabInfo
        self.notebook = None
        tabName, self.currentTabGUI = self.findInitialCurrentTab()
        self.diag.info("Current page set to '" + tabName + "'")

    def findInitialCurrentTab(self):
        return self.tabInfo[0]

    def createView(self):
        self.notebook = gtk.Notebook()
        for tabName, tabGUI in self.tabInfo:
            label = gtk.Label(tabName)
            page = self.createPage(tabGUI, tabName)
            self.notebook.append_page(page, label)

        scriptEngine.monitorNotebook(self.notebook, self.scriptTitle)
        self.notebook.set_scrollable(True)
        self.notebook.show()
        return self.notebook

    def createPage(self, tabGUI, tabName):
        self.diag.info("Adding page " + tabName)
        return tabGUI.createView()

    def shouldShowCurrent(self, *args):
        for name, tabGUI in self.tabInfo:
            if tabGUI.shouldShowCurrent(*args):
                return True
        return False



# Notebook GUI that adds and removes tabs as appropriate...
class ChangeableNotebookGUI(NotebookGUI):
    def createPage(self, tabGUI, tabName):
        page = NotebookGUI.createPage(self, tabGUI, tabName)
        if not tabGUI.shouldShowCurrent():
            self.diag.info("Hiding page " + tabName)
            page.hide()
        return page

    def findInitialCurrentTab(self):
        for tabName, tabGUI in self.tabInfo:
            if tabGUI.shouldShowCurrent():
                return tabName, tabGUI

        return self.tabInfo[0]

    def findFirstRemaining(self, pagesRemoved):
        for page in self.notebook.get_children():
            if page.get_property("visible"):
                pageNum = self.notebook.page_num(page)
                if not pagesRemoved.has_key(pageNum):
                    return pageNum

    def showNewPages(self, *args):
        changed = False
        for pageNum, (name, tabGUI) in enumerate(self.tabInfo):
            page = self.notebook.get_nth_page(pageNum)
            if tabGUI.shouldShowCurrent(*args):
                if not page.get_property("visible"):
                    self.diag.info("Showing page " + name)
                    page.show()
                    changed = True
            else:
                self.diag.info("Remaining hidden " + name)
        return changed
    def setCurrentPage(self, newNum):
        newName, newTabGUI = self.tabInfo[newNum]
        self.diag.info("Resetting for current page " + repr(self.notebook.get_current_page()) + \
                       " to page " + repr(newNum) + " = " + repr(newName))
        self.notebook.set_current_page(newNum)
        # Must do this afterwards, otherwise the above change doesn't propagate
        self.currentTabGUI = newTabGUI
        self.diag.info("Resetting done.")

    def findPagesToHide(self, *args):
        pages = seqdict()
        for pageNum, (name, tabGUI) in enumerate(self.tabInfo):
            page = self.notebook.get_nth_page(pageNum)
            if not tabGUI.shouldShowCurrent(*args) and page.get_property("visible"):
                pages[pageNum] = page
        return pages

    def hideOldPages(self, *args):
        # Must reset the current page before removing it if we're viewing a removed page
        # otherwise we can output lots of pages we don't really look at
        pagesToHide = self.findPagesToHide(*args)
        if len(pagesToHide) == 0:
            return False

        if pagesToHide.has_key(self.notebook.get_current_page()):
            newCurrentPageNum = self.findFirstRemaining(pagesToHide)
            if newCurrentPageNum is not None:
                self.setCurrentPage(newCurrentPageNum)

        # remove from the back, so we don't momentarily view them all if removing everything
        for page in reversed(pagesToHide.values()):
            self.diag.info("Hiding page " + self.notebook.get_tab_label_text(page))
            page.hide()
        return True

    def updateCurrentPage(self, rowCount):
        for pageNum, (tabName, tabGUI) in enumerate(self.tabInfo):
            if tabGUI.shouldShowCurrent() and tabGUI.forceVisible(rowCount):
                self.setCurrentPage(pageNum)

    def notifyNewTestSelection(self, tests, apps, rowCount, direct):
        self.diag.info("New selection with " + repr(tests) + ", adjusting '" + self.scriptTitle + "'")
        # only change pages around if a test is directly selected
        self.updatePages(rowCount=rowCount, changeCurrentPage=direct)

    def updatePages(self, test=None, state=None, rowCount=0, changeCurrentPage=False):
        if not self.notebook:
            return
        pagesShown = self.showNewPages(test, state)
        pagesHidden = self.hideOldPages(test, state)
        if changeCurrentPage:
            self.updateCurrentPage(rowCount)

    def notifyLifecycleChange(self, test, state, changeDesc):
        self.updatePages(test, state)

    def addSuites(self, suites):
        self.updatePages()



class PaneGUI(guiplugins.ContainerGUI):
    def __init__(self, gui1, gui2 , horizontal, shrink=True):
        guiplugins.ContainerGUI.__init__(self, [ gui1, gui2 ])
        self.horizontal = horizontal
        self.panedTooltips = gtk.Tooltips()
        self.paned = None
        self.separatorHandler = None
        self.position = 0
        self.maxPosition = 0
        self.shrink = shrink

    def getSeparatorPositionFromConfig(self):
        if self.horizontal:
            return float(guiConfig.getWindowOption("vertical_separator_position"))
        else:
            return float(guiConfig.getWindowOption("horizontal_separator_position"))

    def createPaned(self):
        if self.horizontal:
            return gtk.HPaned()
        else:
            return gtk.VPaned()

    def scriptCommand(self):
        if self.horizontal:
            return "drag vertical pane separator so left half uses"
        else:
            return "drag horizonal pane separator so top half uses"

    def createView(self):
        self.paned = self.createPaned()
        self.separatorHandler = self.paned.connect('notify::max-position', self.adjustSeparator)
        scriptEngine.registerPaned(self.paned, self.scriptCommand())
        frames = []
        for subgui in self.subguis:
            frame = gtk.Frame()
            frame.set_shadow_type(gtk.SHADOW_IN)
            frame.add(subgui.createView())
            frame.show()
            frames.append(frame)

        self.paned.pack1(frames[0], resize=True)
        self.paned.pack2(frames[1], resize=True)
        self.paned.show()
        return self.paned

    def adjustSeparator(self, *args):
        self.initialMaxSize = self.paned.get_property("max-position")
        self.paned.child_set_property(self.paned.get_child1(), "shrink", self.shrink)
        self.paned.child_set_property(self.paned.get_child2(), "shrink", self.shrink)
        self.position = int(self.initialMaxSize * self.getSeparatorPositionFromConfig())

        self.paned.set_position(self.position)
        # Only want to do this once, when we're visible...
        if self.position > 0:
            self.paned.disconnect(self.separatorHandler)
            # subsequent changes are hopefully manual, and in these circumstances we don't want to prevent shrinking
            if not self.shrink:
                self.paned.connect('notify::position', self.checkShrinkSetting)
        
    def checkShrinkSetting(self, *args):
        oldPos = self.position
        self.position = self.paned.get_position()
        if self.position > oldPos and self.position == self.paned.get_property("min-position"):
            self.paned.set_position(self.position + 1)
        elif self.position < oldPos and self.position == self.paned.get_property("max-position"):
            self.paned.set_position(self.position - 1)
        elif self.position <= oldPos and self.position <= self.paned.get_property("min-position"):
            self.paned.child_set_property(self.paned.get_child1(), "shrink", True)
        elif self.position >= oldPos and self.position >= self.paned.get_property("max-position"):
            self.paned.child_set_property(self.paned.get_child2(), "shrink", True)


class TextViewGUI(guiplugins.SubGUI):
    hovering_over_link = False
    hand_cursor = gtk.gdk.Cursor(gtk.gdk.HAND2)
    regular_cursor = gtk.gdk.Cursor(gtk.gdk.XTERM)

    def __init__(self):
        guiplugins.SubGUI.__init__(self)
        self.text = ""
        self.showingSubText = False
        self.view = None
        
    def shouldShowCurrent(self, *args):
        return len(self.text) > 0        
                    
    def updateView(self):
        if self.view:
            self.updateViewFromText(self.text)

    def createView(self):
        self.view = gtk.TextView()
        self.view.set_name(self.getTabTitle())
        self.view.set_editable(False)
        self.view.set_cursor_visible(False)
        self.view.set_wrap_mode(gtk.WRAP_WORD)
        self.updateViewFromText(self.text)
        self.view.show()
        return self.addScrollBars(self.view, hpolicy=gtk.POLICY_AUTOMATIC)

    def hasStem(self, line, files):
        for fileName, comp in files:
            if comp.stem and line.find(" " + comp.stem + " ") != -1:
                return True
        return False

    def makeSubText(self, files):
        enabled = True
        usedSection, ignoredSection = False, False
        newText = ""
        for line in self.text.splitlines():
            if line.startswith("----"):
                enabled = self.hasStem(line, files)
                if enabled:
                    usedSection = True
                else:
                    ignoredSection = True
            if enabled:
                newText += line + "\n"
        return newText, usedSection and ignoredSection

    def notifyNewFileSelection(self, files):
        if len(files) == 0:
            if self.showingSubText:
                self.showingSubText = False
                self.updateViewFromText(self.text)
        else:
            newText, changed = self.makeSubText(files)
            if changed:
                self.showingSubText = True
                self.updateViewFromText(newText)
            elif self.showingSubText:
                self.showingSubText = False
                self.updateViewFromText(self.text)

    def updateViewFromText(self, text):
        textbuffer = self.view.get_buffer()
        # Encode to UTF-8, necessary for gtk.TextView
        textToUse = guiplugins.convertToUtf8(text)
        if "http://" in textToUse:
            self.view.connect("event-after", self.event_after)
            self.view.connect("motion-notify-event", self.motion_notify_event)
            self.setHyperlinkText(textbuffer, textToUse)
        else:
            textbuffer.set_text(textToUse)

    # Links can be activated by clicking. Low-level code lifted from Maik Hertha's
    # GTK hypertext demo
    def event_after(self, text_view, event): # pragma : no cover - external code and untested browser code
        if event.type != gtk.gdk.BUTTON_RELEASE:
            return False
        if event.button != 1:
            return False
        buffer = text_view.get_buffer()

        # we shouldn't follow a link if the user has selected something
        try:
            start, end = buffer.get_selection_bounds()
        except ValueError:
            # If there is nothing selected, None is return
            pass
        else:
            if start.get_offset() != end.get_offset():
                return False

        x, y = text_view.window_to_buffer_coords(gtk.TEXT_WINDOW_WIDGET, int(event.x), int(event.y))
        iter = text_view.get_iter_at_location(x, y)
        target = self.findLinkTarget(iter)
        if target:
            if os.name == "nt" and not os.environ.has_key("BROWSER"):
                self.notify("Status", "Opening " + target + " in default browser.")
                os.startfile(target)
            else:
                browser = os.getenv("BROWSER", "firefox")
                cmdArgs = [ browser, target ]
                self.notify("Status", 'Started "' + " ".join(cmdArgs) + '" in background.')
                subprocess.Popen(cmdArgs)

        return False

    # Looks at all tags covering the position (x, y) in the text view,
    # and if one of them is a link, change the cursor to the "hands" cursor
    # typically used by web browsers.
    def set_cursor_if_appropriate(self, text_view, x, y): # pragma : no cover - external code
        hovering = False

        buffer = text_view.get_buffer()
        iter = text_view.get_iter_at_location(x, y)

        hovering = bool(self.findLinkTarget(iter))
        if hovering != self.hovering_over_link:
            self.hovering_over_link = hovering

        if self.hovering_over_link:
            text_view.get_window(gtk.TEXT_WINDOW_TEXT).set_cursor(self.hand_cursor)
        else:
            text_view.get_window(gtk.TEXT_WINDOW_TEXT).set_cursor(self.regular_cursor)

    def findLinkTarget(self, iter): # pragma : no cover - called by external code
        tags = iter.get_tags()
        for tag in tags:
            target = tag.get_data("target")
            if target:
                return target

    # Update the cursor image if the pointer moved.
    def motion_notify_event(self, text_view, event): # pragma : no cover - external code
        x, y = text_view.window_to_buffer_coords(gtk.TEXT_WINDOW_WIDGET,
            int(event.x), int(event.y))
        self.set_cursor_if_appropriate(text_view, x, y)
        text_view.window.get_pointer()
        return False

    def setHyperlinkText(self, buffer, text):
        buffer.set_text("", 0)
        iter = buffer.get_iter_at_offset(0)
        for line in text.splitlines():
            if line.find("URL=http://") != -1:
                self.insertLinkLine(buffer, iter, line)
            else:
                buffer.insert(iter, line + "\n")

    def insertLinkLine(self, buffer, iter, line):
        # Assumes text description followed by link
        tag = buffer.create_tag(None, foreground="blue", underline=pango.UNDERLINE_SINGLE)
        words = line.strip().split()
        linkTarget = words[-1][4:] # strip off the URL=
        newLine = " ".join(words[:-1]) + "\n"
        tag.set_data("target", linkTarget)
        buffer.insert_with_tags(iter, newLine, tag)


class RunInfoGUI(TextViewGUI):
    def __init__(self, dynamic):
        TextViewGUI.__init__(self)
        self.dynamic = dynamic
        self.text = "Information will be available here when all tests have been read..."

    def getTabTitle(self):
        return "Run Info"

    def getGroupTabTitle(self):
        return self.getTabTitle()

    def shouldShow(self):
        return self.dynamic

    def appInfo(self, suite):
        textToUse  = "Application name : " + suite.app.fullName() + "\n"
        textToUse += "Version          : " + suite.app.getFullVersion() + "\n"
        textToUse += "Number of tests  : " + str(suite.size()) + "\n"
        textToUse += "Executable       : " + suite.getConfigValue("executable") + "\n"
        return textToUse

    def notifyAnnotate(self, text):
        self.text += "Annotated        : " + text
        self.updateView()

    def notifyAllRead(self, suites):
        self.text = "\n".join(map(self.appInfo, suites)) + "\n"
        self.text += "Command line     : " + plugins.commandLineString(sys.argv) + "\n\n"
        self.text += "Start time       : " + plugins.startTimeString() + "\n"
        self.updateView()

    def notifyAllComplete(self):
        self.text += "End time         : " + plugins.localtime() + "\n"
        self.updateView()


class TestRunInfoGUI(TextViewGUI):
    def __init__(self, dynamic):
        TextViewGUI.__init__(self)
        self.dynamic = dynamic
        self.currentTest = None
        self.resetText()

    def shouldShow(self):
        return self.dynamic

    def getTabTitle(self):
        return "Test Run Info"

    def notifyNewTestSelection(self, tests, *args):
        if len(tests) == 0:
            self.currentTest = None
            self.resetText()
        elif self.currentTest not in tests:
            self.currentTest = tests[0]
            self.resetText()

    def resetText(self):
        self.text = "Selected test  : "
        if self.currentTest:
            self.text += self.currentTest.name + "\n"
            self.appendTestInfo(self.currentTest)
        else:
            self.text += "none\n"
        self.updateView()

    def appendTestInfo(self, test):
        self.text += test.getDescription() + "\n\n"
        self.text += test.app.getRunDescription(test)


class TextInfoGUI(TextViewGUI):
    def __init__(self):
        TextViewGUI.__init__(self)
        self.currentTest = None

    def getTabTitle(self):
        return "Text Info"

    def forceVisible(self, rowCount):
        return rowCount == 1

    def resetText(self, state):
        self.text = ""
        freeText = state.getFreeText()
        if state.isComplete():
            self.text = "Test " + repr(state) + "\n"
            if len(freeText) == 0:
                self.text = self.text.replace(" :", "")
        self.text += str(freeText)
        if state.hasStarted() and not state.isComplete():
            self.text += "\n\nTo obtain the latest progress information and an up-to-date comparison of the files above, " + \
                         "perform 'recompute status' (press '" + \
                         guiConfig.getCompositeValue("gui_accelerators", "recompute_status") + "')"

    def notifyNewTestSelection(self, tests, *args):
        if len(tests) == 0:
            self.currentTest = None
        elif self.currentTest not in tests:
            self.currentTest = tests[0]
            self.resetText(self.currentTest.stateInGui)
            self.updateView()

    def notifyDescriptionChange(self, test):
        self.resetText(self.currentTest.stateInGui)
        self.updateView()

    def notifyLifecycleChange(self, test, state, changeDesc):
        if not test is self.currentTest:
            return
        self.resetText(state)
        self.updateView()


class ProgressBarGUI(guiplugins.SubGUI):
    def __init__(self, dynamic, testCount):
        guiplugins.SubGUI.__init__(self)
        self.dynamic = dynamic
        self.totalNofTests = testCount
        self.addedCount = 0
        self.nofCompletedTests = 0
        self.widget = None

    def shouldShow(self):
        return self.dynamic
    
    def createView(self):
        self.widget = gtk.ProgressBar()
        self.resetBar()
        self.widget.show()
        return self.widget

    def notifyAdd(self, test, initial):
        if test.classId() == "test-case":
            self.addedCount += 1
            if self.addedCount > self.totalNofTests:
                self.totalNofTests += 1
                self.resetBar()
    def notifyAllRead(self, *args):
        # The initial number was told be the static GUI, treat it as a guess
        # Can be wrong in case versions are defined by testsuite files.
        self.totalNofTests = self.addedCount
        self.resetBar()

    def notifyLifecycleChange(self, test, state, changeDesc):
        if changeDesc == "complete":
            self.nofCompletedTests += 1
            self.resetBar()

    def computeFraction(self):
        if self.totalNofTests > 0:
            return float(self.nofCompletedTests) / float(self.totalNofTests)
        else:
            return 0 # No tests yet, haven't read them in

    def resetBar(self):
        if self.widget:
            self.widget.set_text(self.getFractionMessage())
            self.widget.set_fraction(self.computeFraction())

    def getFractionMessage(self):
        if self.nofCompletedTests >= self.totalNofTests:
            completionTime = plugins.localtime()
            return "All " + str(self.totalNofTests) + " tests completed at " + completionTime
        else:
            return str(self.nofCompletedTests) + " of " + str(self.totalNofTests) + " tests completed"

class ClassificationTree(seqdict):
    def addClassification(self, path):
        prevElement = None
        for element in path:
            if not self.has_key(element):
                self[element] = []
            if prevElement and element not in self[prevElement]:
                self[prevElement].append(element)
            prevElement = element

# Class that keeps track of (and possibly shows) the progress of
# pending/running/completed tests
class TestProgressMonitor(guiplugins.SubGUI):
    def __init__(self, dynamic, testCount):
        guiplugins.SubGUI.__init__(self)
        self.classifications = {} # map from test to list of iterators where it exists

        # Each row has 'type', 'number', 'show', 'tests'
        self.treeModel = gtk.TreeStore(gobject.TYPE_STRING, gobject.TYPE_INT, gobject.TYPE_BOOLEAN, \
                                       gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_PYOBJECT)
        self.diag = logging.getLogger("Progress Monitor")
        self.progressReport = None
        self.treeView = None
        self.dynamic = dynamic
        self.testCount = testCount
        self.diffStore = {}
        if self.shouldShow():
            # It isn't really a gui configuration, and this could cause bugs when several apps
            # using differnt diff tools are run together. However, this isn't very likely and we prefer not
            # to recalculate all the time...
            diffTool = guiConfig.getValue("text_diff_program")
            self.diffFilterGroup = plugins.TextTriggerGroup(guiConfig.getCompositeValue("text_diff_program_filters", diffTool))
            if testCount > 0:
                colour = guiConfig.getTestColour("not_started")
                visibility = guiConfig.showCategoryByDefault("not_started")
                self.addNewIter("Not started", None, colour, visibility, testCount)
    def getGroupTabTitle(self):
        return "Status"
    def shouldShow(self):
        return self.dynamic
    def createView(self):
        self.treeView = gtk.TreeView(self.treeModel)
        self.treeView.set_name("Test Status View")
        selection = self.treeView.get_selection()
        selection.set_mode(gtk.SELECTION_MULTIPLE)
        selection.set_select_function(self.canSelect)
        selection.connect("changed", self.selectionChanged)
        textRenderer = gtk.CellRendererText()
        textRenderer.set_property('wrap-width', 350)
        textRenderer.set_property('wrap-mode', pango.WRAP_WORD_CHAR)
        numberRenderer = gtk.CellRendererText()
        numberRenderer.set_property('xalign', 1)
        statusColumn = gtk.TreeViewColumn("Status", textRenderer, text=0, background=3, font=4)
        numberColumn = gtk.TreeViewColumn("Number", numberRenderer, text=1, background=3, font=4)
        statusColumn.set_resizable(True)
        numberColumn.set_resizable(True)
        self.treeView.append_column(statusColumn)
        self.treeView.append_column(numberColumn)
        toggle = gtk.CellRendererToggle()
        toggle.set_property('activatable', True)
        scriptEngine.registerCellToggleButton(toggle, "toggle progress report category", self.treeView)
        toggle.connect("toggled", self.showToggled)
        scriptEngine.monitor("set progress report filter selection to", selection)
        toggleColumn = gtk.TreeViewColumn("Visible", toggle, active=2)
        toggleColumn.set_resizable(True)
        toggleColumn.set_alignment(0.5)
        self.treeView.append_column(toggleColumn)
        self.treeView.show()
        return self.addScrollBars(self.treeView, hpolicy=gtk.POLICY_NEVER)
    def canSelect(self, path):
        pathIter = self.treeModel.get_iter(path)
        return self.treeModel.get_value(pathIter, 2)
    def notifyAdd(self, test, initial):
        if self.dynamic and test.classId() == "test-case":
            incrementCount = self.testCount == 0
            self.insertTest(test, test.stateInGui, incrementCount)
    def notifyAllRead(self, *args):
        # Fix the not started count in case the initial guess was wrong
        if self.testCount > 0:
            self.diag.info("Reading complete, updating not-started count to actual answer")
            iter = self.treeModel.get_iter_root()
            actualTestCount = len(self.treeModel.get_value(iter, 5))
            measuredTestCount = self.treeModel.get_value(iter, 1)
            if actualTestCount != measuredTestCount:
                self.treeModel.set_value(iter, 1, actualTestCount)
    def selectionChanged(self, selection):
        # For each selected row, select the corresponding rows in the test treeview
        tests = []
        selection.selected_foreach(self.selectCorrespondingTests, tests)
        self.notify("SetTestSelection", tests)
    def selectCorrespondingTests(self, treemodel, path, iter, tests , *args):
        guilog.info("Selecting all " + str(treemodel.get_value(iter, 1)) + " tests in category " + treemodel.get_value(iter, 0))
        for test in treemodel.get_value(iter, 5):
            if test not in tests:
                tests.append(test)
    def findTestIterators(self, test):
        return self.classifications.get(test, [])
    def getCategoryDescription(self, state, categoryName=None):
        if not categoryName:
            categoryName = state.category
        briefDesc, fullDesc = state.categoryDescriptions.get(categoryName, (categoryName, categoryName))
        return briefDesc.replace("_", " ").capitalize()

    def filterDiff(self, test, diff):
        filteredDiff = ""
        for line in diff.split("\n"):
            if self.diffFilterGroup.stringContainsText(line):
                filteredDiff += line + "\n"
        return filteredDiff

    def getClassifiers(self, test, state):
        classifiers = ClassificationTree()
        catDesc = self.getCategoryDescription(state)
        if state.isMarked():
            if state.briefText == catDesc:
                # Just in case - otherwise we get an infinite loop...
                classifiers.addClassification([ catDesc, "Marked as Marked" ])
            else:
                classifiers.addClassification([ catDesc, state.briefText ])
            return classifiers

        if not state.isComplete() or not state.hasFailed():
            classifiers.addClassification([ catDesc ])
            return classifiers

        if not state.isSaveable() or state.warnOnSave(): # If it's not saveable, don't classify it by the files
            overall, details = state.getTypeBreakdown()
            self.diag.info("Adding unsaveable : " + catDesc + " " + details)
            classifiers.addClassification([ "Failed", catDesc, details ])
            return classifiers

        comparisons = state.getComparisons()
        maxLengthForGrouping = test.getConfigValue("lines_of_text_difference")
        for fileComp in filter(lambda c: c.getType() == "failure", comparisons):
            summary = fileComp.getSummary(includeNumbers=False)
            fileClass = [ "Failed", "Differences", summary ]

            freeText = fileComp.getFreeTextBody()
            if freeText.count("\n") < maxLengthForGrouping:
                filteredDiff = self.filterDiff(test, freeText)
                summaryDiffs = self.diffStore.setdefault(summary, seqdict())
                testList, hasGroup = summaryDiffs.setdefault(filteredDiff, ([], False))
                if test not in testList:
                    testList.append(test)
                if len(testList) > 1 and not hasGroup:
                    hasGroup = True
                    summaryDiffs[filteredDiff] = (testList, hasGroup)
                if hasGroup:
                    group = summaryDiffs.index(filteredDiff) + 1
                    fileClass.append("Group " + str(group))

            self.diag.info("Adding file classification for " + repr(fileComp) + " = " + repr(fileClass))
            classifiers.addClassification(fileClass)

        for fileComp in filter(lambda c: c.getType() != "failure", comparisons):
            summary = fileComp.getSummary(includeNumbers=False)
            fileClass = [ "Failed", "Performance differences", self.getCategoryDescription(state, summary) ]
            self.diag.info("Adding file classification for " + repr(fileComp) + " = " + repr(fileClass))
            classifiers.addClassification(fileClass)

        return classifiers

    def removeFromModel(self, test):
        for iter in self.findTestIterators(test):
            testCount = self.treeModel.get_value(iter, 1)
            self.treeModel.set_value(iter, 1, testCount - 1)
            if testCount == 1:
                self.treeModel.set_value(iter, 3, "white")
                self.treeModel.set_value(iter, 4, "")
            allTests = self.treeModel.get_value(iter, 5)
            allTests.remove(test)
            self.diag.info("Removing test " + repr(test) + " from node " + self.treeModel.get_value(iter, 0))
            self.treeModel.set_value(iter, 5, allTests)

    def removeFromDiffStore(self, test):
        for fileInfo in self.diffStore.values():
            for testList, hasGroup in fileInfo.values():
                if test in testList:
                    testList.remove(test)

    def insertTest(self, test, state, incrementCount):
        self.classifications[test] = []
        classifiers = self.getClassifiers(test, state)
        nodeClassifier = classifiers.keys()[0]
        defaultColour, defaultVisibility = self.getCategorySettings(state.category, nodeClassifier, classifiers)
        return self.addTestForNode(test, defaultColour, defaultVisibility, nodeClassifier, classifiers, incrementCount)
    def getCategorySettings(self, category, nodeClassifier, classifiers):
        # Use the category description if there is only one level, otherwise rely on the status names
        if len(classifiers.get(nodeClassifier)) == 0 or category == "failure":
            return guiConfig.getTestColour(category), guiConfig.showCategoryByDefault(category)
        else:
            return None, True
    def updateTestAppearance(self, test, state, changeDesc, colour):
        resultType, summary = state.getTypeBreakdown()
        catDesc = self.getCategoryDescription(state, resultType)
        mainColour = guiConfig.getTestColour(catDesc, guiConfig.getTestColour(resultType))
        # Don't change suite states when unmarking tests
        updateSuccess = state.hasSucceeded() and changeDesc != "unmarked"
        saved = changeDesc.find("save") != -1
        self.notify("TestAppearance", test, summary, mainColour, colour, updateSuccess, saved)
        self.notify("Visibility", [ test ], self.shouldBeVisible(test))

    def getInitialTestsForNode(self, test, parentIter, nodeClassifier):
        try:
            if nodeClassifier.startswith("Group "):
                diffNumber = int(nodeClassifier[6:]) - 1
                parentName = self.treeModel.get_value(parentIter, 0)
                testLists = self.diffStore.get(parentName)
                testList, hasGroup = testLists.values()[diffNumber]
                return copy(testList)
        except ValueError:
            pass
        return [ test ]

    def addTestForNode(self, test, defaultColour, defaultVisibility, nodeClassifier, classifiers, incrementCount, parentIter=None):
        nodeIter = self.findIter(nodeClassifier, parentIter)
        colour = guiConfig.getTestColour(nodeClassifier, defaultColour)
        if nodeIter:
            visibility = self.treeModel.get_value(nodeIter, 2)
            self.diag.info("Adding " + repr(test) + " for node " + nodeClassifier + ", visible = " + repr(visibility))
            self.insertTestAtIter(nodeIter, test, colour, incrementCount)
            self.classifications[test].append(nodeIter)
        else:
            visibility = guiConfig.showCategoryByDefault(nodeClassifier, parentHidden=not defaultVisibility)
            initialTests = self.getInitialTestsForNode(test, parentIter, nodeClassifier)
            nodeIter = self.addNewIter(nodeClassifier, parentIter, colour, visibility, len(initialTests), initialTests)
            for initTest in initialTests:
                self.diag.info("New node " + nodeClassifier + ", visible = " + repr(visibility) + " : add " + repr(initTest))
                self.classifications[initTest].append(nodeIter)

        subColours = []
        for subNodeClassifier in classifiers[nodeClassifier]:
            subColour = self.addTestForNode(test, colour, visibility, subNodeClassifier, classifiers, incrementCount, nodeIter)
            subColours.append(subColour)

        if len(subColours) > 0:
            return subColours[0]
        else:
            return colour
    def insertTestAtIter(self, iter, test, colour, incrementCount):
        allTests = self.treeModel.get_value(iter, 5)
        testCount = self.treeModel.get_value(iter, 1)
        if testCount == 0:
            self.treeModel.set_value(iter, 3, colour)
            self.treeModel.set_value(iter, 4, "bold")
        if incrementCount:
            self.treeModel.set_value(iter, 1, testCount + 1)
        self.diag.info("Tests for node " + self.treeModel.get_value(iter, 0) + " " + repr(allTests))
        allTests.append(test)
        self.diag.info("Tests for node " + self.treeModel.get_value(iter, 0) + " " + repr(allTests))
    def addNewIter(self, classifier, parentIter, colour, visibility, testCount, tests=[]):
        modelAttributes = [classifier, testCount, visibility, colour, "bold", tests]
        newIter = self.treeModel.append(parentIter, modelAttributes)
        if parentIter:
            self.treeView.expand_row(self.treeModel.get_path(parentIter), open_all=0)
        return newIter
    def findIter(self, classifier, startIter):
        iter = self.treeModel.iter_children(startIter)
        while iter != None:
            name = self.treeModel.get_value(iter, 0)
            if name == classifier:
                return iter
            else:
                iter = self.treeModel.iter_next(iter)
    def notifyLifecycleChange(self, test, state, changeDesc):
        self.removeFromModel(test)
        if changeDesc.find("save") != -1 or changeDesc.find("marked") != -1 or changeDesc.find("recalculated") != -1:
            self.removeFromDiffStore(test)
        colourInserted = self.insertTest(test, state, incrementCount=True)
        self.updateTestAppearance(test, state, changeDesc, colourInserted)
        
    def removeParentIters(self, iters):
        noParents = []
        for iter1 in iters:
            if not self.isParent(iter1, iters):
                noParents.append(iter1)
        return noParents

    def isParent(self, iter1, iters):
        path1 = self.treeModel.get_path(iter1)
        for iter2 in iters:
            parent = self.treeModel.iter_parent(iter2)
            if parent is not None and self.treeModel.get_path(parent) == path1:
                return True
        return False

    def shouldBeVisible(self, test):
        iters = self.findTestIterators(test)
        # ignore the parent nodes where visibility is concerned
        visibilityIters = self.removeParentIters(iters)
        self.diag.info("Visibility for " + repr(test) + " : iters " + repr(map(self.treeModel.get_path, visibilityIters)))
        for nodeIter in visibilityIters:
            visible = self.treeModel.get_value(nodeIter, 2)
            if visible:
                return True
        return False
    
    def getAllChildIters(self, iter):
         # Toggle all children too
        childIters = []
        childIter = self.treeModel.iter_children(iter)
        while childIter != None:
            childIters.append(childIter)
            childIters += self.getAllChildIters(childIter)
            childIter = self.treeModel.iter_next(childIter)
        return childIters

    def showToggled(self, cellrenderer, path):
        # Toggle the toggle button
        newValue = not self.treeModel[path][2]
        self.treeModel[path][2] = newValue

        iter = self.treeModel.get_iter_from_string(path)
        categoryName = self.treeModel.get_value(iter, 0)
        for childIter in self.getAllChildIters(iter):
            self.treeModel.set_value(childIter, 2, newValue)

        if categoryName == "Not started":
            self.notify("DefaultVisibility", newValue)

        changedTests = []
        for test in self.treeModel.get_value(iter, 5):
            if self.shouldBeVisible(test) == newValue:
                changedTests.append(test)
        self.notify("Visibility", changedTests, newValue)

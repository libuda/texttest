
import os, shutil, plugins, operator
from glob import glob
from itertools import groupby

# Trawl around for a suitable dir to reconnect to if we haven't been told one
# A tangle of side-effects: we find the run directory when asked for the extra versions,
# (so we can provide further ones accordingly), find the application directory when asked to check sanity
# (so we can bail if it's not there) and store in self.reconnDir, ready to provide to the ReconnectTest action
class ReconnectConfig:
    runDirCache = {}
    datedVersions = set()
    def __init__(self, optionMap):
        self.fullRecalculate = optionMap.has_key("reconnfull")
        self.diag = plugins.getDiagnostics("Reconnection")
        self.reconnectTmpInfo = optionMap.get("reconnect")
        self.reconnDir = None
        self.errorMessage = ""

    def getReconnectAction(self):
        return ReconnectTest(self.reconnDir, self.fullRecalculate)

    def cacheRunDir(self, app, runDir, version=""):
        if version:
            keys = [ app.fullName + "." + version ]
        else:
            keys = [ app.fullName ] + app.versions
        for i in range(len(keys)):
            subKey = ".".join(keys[:i+1])
            if i == len(keys) - 1 or not self.runDirCache.has_key(subKey):
                self.runDirCache[subKey] = runDir
                self.diag.info("Caching " + subKey + " = " + runDir)

    def findRunDir(self, app):
        return self._findRunDir(repr(app))

    def _findRunDir(self, searchKey):
        self.diag.info("Searching for run directory for " + searchKey)
        entry = self.runDirCache.get(searchKey)
        if entry:
            return entry
        parts = searchKey.split(".")
        if len(parts) > 1:
            return self._findRunDir(".".join(parts[:-1]))

    def getExtraVersions(self, app):
        self.diag.info("Finding reconnect directory for " + repr(app) + " under " + repr(self.reconnectTmpInfo))
        if self.reconnectTmpInfo and os.path.isdir(self.reconnectTmpInfo):
            # See if this is an explicitly provided run directory
            versionSets = self.getVersionSetsTopDir(self.reconnectTmpInfo)
            self.diag.info("Directory has version sets " + repr(versionSets))
            if versionSets is not None:
                return self.getVersionsFromDirs(app, [ self.reconnectTmpInfo ])

        fetchDir = app.getPreviousWriteDirInfo(self.reconnectTmpInfo)
        if not os.path.isdir(fetchDir):
            if fetchDir == self.reconnectTmpInfo or not self.reconnectTmpInfo:
                self.errorMessage = "Could not find TextTest temporary directory at " + fetchDir
            else:
                self.errorMessage = "Could not find TextTest temporary directory for " + \
                                    self.reconnectTmpInfo + " at " + fetchDir
            return []

        self.diag.info("Looking for run directories under " + fetchDir)
        runDirs = self.getReconnectRunDirs(app, fetchDir)
        self.diag.info("Found run directories " + repr(runDirs))
        if len(runDirs) == 0:
            self.errorMessage = "Could not find any runs matching " + app.description() + " under " + fetchDir
            return []
        else:
            return self.getVersionsFromDirs(app, runDirs)

    def versionsCorrect(self, app, dirName):
        versionSets = self.getVersionSetsTopDir(dirName)
        self.diag.info("Directory has version sets " + repr(versionSets))
        if versionSets is None:
            return False
        appVersionSet = frozenset(app.versions)
        return reduce(operator.or_, (appVersionSet.issubset(s) for s in versionSets), False)
    
    def findAppDirUnder(self, app, runDir):
        # Don't pay attention to dated versions here...
        appVersions = frozenset(app.versions).difference(self.datedVersions)
        self.diag.info("Looking for directory with versions " + repr(appVersions))
        for f in os.listdir(runDir):
            versionSet = self.getVersionSetSubDir(f, app.name)
            if versionSet == appVersions:
                return os.path.join(runDir, f)
    
    def getReconnectRunDirs(self, app, fetchDir):
        correctNames = filter(lambda f: self.versionsCorrect(app, f), os.listdir(fetchDir))
        correctNames.sort() # Need to do this for determinism, and because itertools.groupby (lower down) requires it
        fullPaths = [ os.path.join(fetchDir, d) for d in correctNames ]
        return filter(lambda d: self.isRunDirectoryFor(app, d), fullPaths)

    def isRunDirectoryFor(self, app, d):
        appDirRoot = os.path.join(d, app.name)
        if os.path.isdir(appDirRoot):
            return True
        else:
            return len(glob(appDirRoot + ".*")) > 0

    def getVersionListsTopDir(self, fileName):
        # Show the framework how to find the version list given a file name
        # If it doesn't match, return None
        parts = os.path.basename(fileName).split(".")
        if len(parts) > 2 and parts[0] != "static_gui":
            # drop the run descriptor at the start and the date/time and pid at the end
            versionParts = ".".join(parts[1:-2]).split("++")
            return [ part.split(".") for part in versionParts ]
            
    def getVersionSetsTopDir(self, fileName):
        vlists = self.getVersionListsTopDir(fileName)
        if vlists is not None:
            return [ frozenset(vlist) for vlist in vlists ]
        
    def getVersionSetSubDir(self, fileName, stem):
        # Show the framework how to find the version list given a file name
        # If it doesn't match, return None
        parts = fileName.split(".")
        if stem == parts[0]:
            # drop the application at the start 
            return frozenset(parts[1:])

    def getVersionsFromDirs(self, app, dirs):
        versions = []
        appVersions = frozenset(app.versions)
        for versionLists, groupDirIter in groupby(dirs, self.getVersionListsTopDir):
            for versionList in versionLists:
                extraVersion = ".".join(frozenset(versionList).difference(appVersions))
                version = ".".join(versionList)
                groupDirs = list(groupDirIter)
                if extraVersion:
                    if len(groupDirs) == 1:
                        versions.append(extraVersion)
                        self.cacheRunDir(app, groupDirs[0], version)
                    else:
                        for dir in groupDirs:
                            datedVersion = os.path.basename(dir).split(".")[-2]
                            self.datedVersions.add(datedVersion)
                            versions.append(extraVersion + "." + datedVersion)
                            self.cacheRunDir(app, dir, version + "." + datedVersion)
                else:
                    self.cacheRunDir(app, groupDirs[0])
                    for dir in groupDirs[1:]:
                        datedVersion = os.path.basename(dir).split(".")[-2]
                        self.datedVersions.add(datedVersion)
                        versions.append(datedVersion)
                        if version:
                            self.cacheRunDir(app, dir, version + "." + datedVersion)
                        else:
                            self.cacheRunDir(app, dir, datedVersion)
        versions.sort()
        return versions

    def checkSanity(self, app):
        if self.errorMessage: # We failed already, basically
            raise plugins.TextTestError, self.errorMessage

        runDir = self.findRunDir(app)
        if not runDir:
            raise plugins.TextTestError, "Could not find any runs matching " + app.description() 
        self.diag.info("Found run directory " + repr(runDir))
        self.reconnDir = self.findAppDirUnder(app, runDir)        
        self.diag.info("Found application directory " + repr(self.reconnDir))
        if not self.reconnDir:
            raise plugins.TextTestError, "Could not find an application directory matching " + app.description() + \
                  " for the run directory found at " + runDir
        for datedVersion in self.datedVersions:
            app.addConfigEntry("unsaveable_version", datedVersion)
        

class ReconnectTest(plugins.Action):
    def __init__(self, rootDirToCopy, fullRecalculate):
        self.rootDirToCopy = rootDirToCopy
        self.fullRecalculate = fullRecalculate
        self.diag = plugins.getDiagnostics("Reconnection")
    def __repr__(self):
        return "Reconnecting to"
    def __call__(self, test):
        newState = self.getReconnectState(test)
        self.describe(test, self.getStateText(newState))
        if newState:
            test.changeState(newState)
    def getReconnectState(self, test):
        reconnLocation = os.path.join(self.rootDirToCopy, test.getRelPath())
        self.diag.info("Reconnecting to test at " + reconnLocation)
        if os.path.isdir(reconnLocation):
            return self.getReconnectStateFrom(test, reconnLocation)
        else:
            return plugins.Unrunnable(briefText="no results", \
                                      freeText="No file found to load results from under " + reconnLocation)
    def getStateText(self, state):
        if state:
            return " (state " + state.category + ")"
        else:
            return " (recomputing)"
    def getReconnectStateFrom(self, test, location):
        stateToUse = None
        stateFile = os.path.join(location, "framework_tmp", "teststate")
        if os.path.isfile(stateFile):
            newTmpPath = os.path.dirname(self.rootDirToCopy)
            loaded, newState = test.getNewState(open(stateFile, "rU"), updatePaths=True, newTmpPath=newTmpPath)
            if loaded and self.modifyState(test, newState): # if we can't read it, recompute it
                stateToUse = newState

        if self.fullRecalculate or not stateToUse:
            self.copyFiles(test, location)

        return stateToUse    
    def copyFiles(self, test, reconnLocation):
        test.makeWriteDirectory()
        for file in os.listdir(reconnLocation):
            fullPath = os.path.join(reconnLocation, file)
            if os.path.isfile(fullPath):
                shutil.copyfile(fullPath, test.makeTmpFileName(file, forComparison=0))

    def modifyState(self, test, newState):
        if self.fullRecalculate:                
            # Only pick up errors here, recalculate the rest. Don't notify until
            # we're done with recalculation.
            if newState.hasResults():
                # Also pick up execution machines, we can't get them otherwise...
                test.state.executionHosts = newState.executionHosts
                return False # don't actually change the state
            else:
                newState.lifecycleChange = "" # otherwise it's regarded as complete
                return True
        else:
            return True
    def setUpApplication(self, app):
        plugins.log.info("Reconnecting to test results in directory " + self.rootDirToCopy)

    def setUpSuite(self, suite):
        self.describe(suite)

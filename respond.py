#!/usr/local/bin/python
import comparetest, ndiff, sys, string, os
    
# Abstract base to make it easier to write test responders
class Responder:
    def __repr__(self):
        return "Responding to"
    def __call__(self, test, description):
        if os.path.isfile("core.Z"):
            os.system("uncompress core.Z")
        if os.path.isfile("core"):
            self.handleCoreFile(test)
            os.remove("core")
        if comparetest.testComparisonMap.has_key(test):
            comparisons = comparetest.testComparisonMap[test]
            print test.getIndent() + repr(test), self, "differences in", self.comparisonsString(comparisons)
            self.handleFailure(test, comparisons)
            del comparetest.testComparisonMap[test]
        else:
            self.handleSuccess(test)
    def handleSuccess(self, test):
        pass
    def comparisonsString(self, comparisons):
        return string.join([repr(x) for x in comparisons], ",")
    def setUpSuite(self, suite, description):
        pass

# Uses the python ndiff library, which should work anywhere. Override display method to use other things
class InteractiveResponder(Responder):
    def __repr__(self):
        return "FAILED :"
    def handleFailure(self, test, comparisons):
        performView = self.askUser(test, comparisons, 1)
        if performView:
            self.displayComparisons(comparisons, sys.stdout, test.app.getConfigValue("log_file"))
            self.askUser(test, comparisons, 0)
    def displayComparisons(self, comparisons, displayStream, logFile):
        for comparison in comparisons:
            displayStream.write("------------------ Differences in " + repr(comparison) + " --------------------\n")
            self.display(comparison, displayStream, logFile)
    def display(self, comparison, displayStream, logFile):
        ndiff.fcompare(comparison.stdCmpFile, comparison.tmpCmpFile)
    def askUser(self, test, comparisons, allowView):      
        options = "Save(s) or continue(any other key)?"
        if len(test.app.version) > 0:
            options = "Save Version " + test.app.version + "(z), " + options
        if allowView:
            options = "View details(v), " + options
        print test.getIndent() + options
        response = sys.stdin.readline();
        if 's' in response:
            for comparison in comparisons:
                comparison.overwrite()
        elif allowView and 'v' in response:
            return 1
        elif 'z' in response:
            for comparison in comparisons:
                comparison.overwrite(test.app.version)
        return 0
            
# Uses UNIX tkdiff
class UNIXInteractiveResponder(InteractiveResponder):
    def __init__(self, lineCount):
        self.lineCount = lineCount
    def handleCoreFile(self, test):
        fileName = "coreCommands.gdb"
        file = open(fileName, "w")
        file.write("bt\nq\n")
        file.close()
        # Yes, we know this is horrible. Does anyone know a better way of getting the binary out of a core file???
        # Unfortunately running gdb is not the answer, because it truncates the data...
        binary = os.popen("csh -c 'echo `tail -c 1024 core`'").read().split(" ")[-1].strip()        
        gdbData = os.popen("gdb -q -x " + fileName + " " + binary + " core")
        for line in gdbData.xreadlines():
            if line.find("Program terminated") != -1:
                print test.getIndent() + repr(test) + " CRASHED (" + line.strip() + ") : stack trace from gdb follows"
            if line[0] == "#":
                print line.strip()
        os.remove(fileName)
    def display(self, comparison, displayStream, logFile):
        argumentString = " " + comparison.stdCmpFile + " " + comparison.tmpCmpFile
        if repr(comparison) == logFile and displayStream == sys.stdout:
            print "<See tkdiff window>"
            os.system("tkdiff" + argumentString + " &")
        else:
            stdin, stdout, stderr = os.popen3("diff" + argumentString)
            linesWritten = 0
            for line in stdout.xreadlines():
                if linesWritten >= self.lineCount:
                    return
                displayStream.write(line)
                linesWritten += 1
    
class OverwriteOnFailures(Responder):
    def __init__(self, version):
        self.version = version
    def __repr__(self):
        return "- overwriting"
    def handleFailure(self, test, comparisons):
        for comparison in comparisons:
            comparison.overwrite(self.version)

# Works only on UNIX
class BatchResponder(Responder):
    def __init__(self, lineCount):
        self.failures = {}
        self.successes = []
        self.mainSuite = None
        self.responder = UNIXInteractiveResponder(lineCount)
    def __del__(self):
        mailFile = os.popen("sendmail -t", "w")
        fromAddress = os.environ["USER"]
        toAddress = self.getRecipient(fromAddress)
        mailFile.write("From: " + fromAddress + os.linesep)
        mailFile.write("To: " + toAddress + os.linesep)
        mailFile.write("Subject: " + self.getMailTitle() + os.linesep)
        mailFile.write(os.linesep) # blank line separating headers from body
        if len(self.successes) > 0:
            mailFile.write("The following tests succeeded : " + os.linesep)
            mailFile.writelines(self.successes)
        if self.failureCount() > 0:
            self.reportFailures(mailFile)
        mailFile.close()
    def getRecipient(self, fromAddress):
        app = self.mainSuite.app
        if fromAddress == app.getConfigValue("nightjob_user"):
            return app.getConfigValue("nightjob_recipients")
        else:
            return fromAddress
    def handleSuccess(self, test):
        self.successes.append(self.testLine(test))
    def handleFailure(self, test, comparisons):
        self.failures[test] = comparisons
    def setUpSuite(self, suite, description):
        if self.mainSuite == None:
            self.mainSuite = suite
    def failureCount(self):
        return len(self.failures)
    def testCount(self):
        return self.failureCount() + len(self.successes)
    def getMailTitle(self):
        suiteDescription = repr(self.mainSuite.app) + " Test Suite (" + self.mainSuite.name + " in " + self.mainSuite.app.checkout + ") : "
        return suiteDescription + str(self.failureCount()) + " out of " + str(self.testCount()) + " tests failed"
    def testLine(self, test):
        return repr(test) + os.linesep
    def reportFailures(self, mailFile):
        mailFile.write(os.linesep + "The following tests failed : " + os.linesep)
        mailFile.writelines(map(self.testLine, self.failures.keys()))
        mailFile.write(os.linesep + "Failure information for the tests that failed follows..." + os.linesep)
        for test in self.failures.keys():
            mailFile.write("--------------------------------------------------------" + os.linesep)
            mailFile.write("TEST FAILED -> " + repr(test) + os.linesep)
            os.chdir(test.abspath)
            self.responder.displayComparisons(self.failures[test], mailFile)
        

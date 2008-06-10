#!/usr/bin/env python

import sys, os, signal

gotSignal = False

def makeSocket():
    import socket
    try:
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except AttributeError: # in case we get interrupted partway through
        reload(socket)
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
def createSocket():
    servAddr = os.getenv("TEXTTEST_MIM_SERVER")
    if servAddr:
        host, port = servAddr.split(":")
        serverAddress = (host, int(port))
        sock = makeSocket()
        sock.connect(serverAddress)
        return sock

def readFromSocket(sock):
    from socket import error
    try:
        return sock.makefile().read()
    except error: # If we're interrupted, try again
        return sock.makefile().read()

def sendServerState(stateDesc):
    sock = createSocket()
    if sock:
        sock.sendall("SUT_SERVER:" + stateDesc + "\n")
        sock.close()

def createAndSend():
    sock = createSocket()
    text = "SUT_COMMAND_LINE:" + repr(sys.argv) + ":SUT_SEP:" + repr(os.environ) + \
           ":SUT_SEP:" + os.getcwd() + ":SUT_SEP:" + str(os.getpid())
    sock.sendall(text)
    return sock

def sendKill(sigNum, *args):
    global gotSignal
    gotSignal = True
    sock = createSocket()
    text = "SUT_COMMAND_KILL:" + str(sigNum) + ":SUT_SEP:" + str(os.getpid())
    sock.sendall(text)
    sock.close()

if os.name == "posix":
    signal.signal(signal.SIGINT, sendKill)
    signal.signal(signal.SIGTERM, sendKill)

if __name__ == "__main__":
    sock = createAndSend()
    sock.shutdown(1)
    response = readFromSocket(sock)
    sock.close()
    try:
        stdout, stderr, exitStr = response.split("|TT_CMD_SEP|")
        sys.stdout.write(stdout)
        sys.stdout.flush()
        sys.stderr.write(stderr)
        sys.stderr.flush()
        exitCode = int(exitStr)
        if exitCode < 0 and not gotSignal:
            # process was killed (on UNIX...)
            # We should hang if we haven't been killed ourselves (though we might be replaying on Windows anyway)
            if os.name == "posix":
                signal.pause()
            else:
                import time
                time.sleep(10000) # As best we can do on Windows where waiting for signals is concerned :)
        sys.exit(exitCode)
    except ValueError:
        sys.stderr.write("Received unexpected communication from MIM server:\n " + response + "\n\n")


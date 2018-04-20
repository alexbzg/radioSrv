#!/usr/bin/python3
#coding=utf-8

import logging, asyncio
from collections import deque


class ControllerProtocol( asyncio.Protocol ):
    timeoutInterval = 1
    pingInterval = 60

    def __init__( self, factory ):
        self.factory = factory
        factory.protocol = self
        self.rcvBuf = ''

    def ping( self ):
        self.queueCmd( '' )
        self.pingTimer = None

    def data_received(self, data):
        strData = data.decode()
        for ch in strData:
            self.rcvBuf += ch
            if ch == '\n':
                self.lineReceived( self.rcvBuf )
                self.rcvBuf = ''

    def lineReceived(self, data):        
        data = data.replace( '#', '' ).replace( '\r\n', '' )
        #logging.info( self.factory.host + ' ' + data )
        if self.pingTimer:
            self.pingTimer.cancel()
            self.pingTimer = None;
        self.pingTimer = self.factory.loop.call_later( self.pingInterval,
                lambda: self.ping() )
        if data.startswith( 'SLINF' ) or data.startswith( 'FLAGS' ) or \
                data.startswith( 'JConfig' ):
            return
        if data.startswith( 'EVT' ) and data != 'EVT,OK':
            discard, line, state = data.rsplit( ',', 2 )
            if self.factory.linesStates:
                self.factory.saveLineState( int( line ), state )
        else:
            if self.currentCmd:
                #logging.info( 'to: ' + self.currentCmd[0] )
                if self.timeout:
                    self.timeout.cancel()
                    self.timeout = None
                if data == 'ERR':
                    logging.error( self.factory.host + \
                        ' error in response to ' + self.currentCmd[0] )
                cmd, cb = self.currentCmd
                self.currentCmd = None
                if data != 'OK' and data != 'ERR':
                    discard, data = data.rsplit( ',', 1 )
                if len( self.cmdQueue ) > 0:
                    self.currentCmd = self.cmdQueue.popleft()
                    self.sendCmd( self.currentCmd[0] )
                if cb:
                    cb( data )

    def connection_made( self, transport ):
        self.transport = transport
        self.recvBuf = ''
        logging.error( self.factory.host + " connection made" )
        self.currentCmd = None
        self.cmdQueue = deque( [] )
        self.timeout = None
        self.pingTimer = None
        self.queueCmd( '' )
        self.queueCmd( "PSW,SET,Jerome", 
                lambda x: self.factory.UARTconnect() )
        self.queueCmd( "EVT,ON" )
        self.queueCmd( "IO,GET,ALL", self.factory.saveLinesDirs )
        self.queueCmd( "RID,ALL", self.factory.saveLinesStates )
        self.factory.setConnected( True )

    def connection_lost( self, reason ):
        logging.error( self.factory.host + " connection lost" )
        self.factory.setConnected( False )
        #self.factory = None

    def sendCmd( self, cmd ):
        #logging.error( self.factory.host + ' ' + cmd )
        fullCmd = cmd
        if cmd != '':
            fullCmd = "," + fullCmd
        fullCmd = "$KE" + fullCmd + "\r\n"
        self.transport.write( fullCmd.encode() )
        self.timeout = self.factory.loop.call_later( self.timeoutInterval, 
                lambda: self.callTimeout() )

    def callTimeout( self ):
        logging.error( self.factory.host + " timeout" )
        self.transport.close()

    def queueCmd( self, cmd, cb = None ):
        if self.currentCmd:
            self.cmdQueue.append( ( cmd, cb ) )
        else:
            self.currentCmd = ( cmd, cb )
            self.sendCmd( cmd )

class UARTProtocol( asyncio.Protocol ):

    def __init__(self, controller):
        self.controller = controller
        controller.UARTconnection = self

    def connection_lost( self, reason ):
        logging.error( self.controller.host + \
                ' UART connection lost or failed ' + \
                str( reason ) )
        self.controller.UARTconnectionLost()

    def connection_made(self, transport):
        self.transport = transport
        self.controller.UARTconnectionMade()

    def data_received(self, data):
        self.controller.UARTdataReceived( data )

class Controller:
    maxDelay = 15
    UARTinterval = 0
   

    def toggleLine( self, line, cb = None ):
        self.setLineState( line, not self.getLineState( line ), cb )

    def setLineState( self, line, state, cb = None ):
        nState = '1' if state else '0'
        self.protocol.queueCmd( 'WR,' + str( line ) + ',' + nState, \
                lambda data: self.setLineStateCB( line, nState, data, 
                    cb ) )

    def setLineDir( self, line, dir, cb = None ):
        self.protocol.queueCmd( 'IO,SET,' + str( line ) + ',' + \
                str( dir ), 
                lambda data: self.setLineDirCB( line, dir, data, cb ) )

    def setLineDirCB( self, line, dir, data, cb = None ):
        if data == 'OK':
            self.linesDirs[ line ] = dir
        if cb:
            cb( data )


    def setLineMode( self, line, mode ):
        self.linesModes[ line ] = mode
        self.checkLineMode( line )

    def checkLineMode( self, line ):
        if line in self.linesModes and \
            len( self.linesDirs ) > line:
            if self.linesModes[ line ] == 'in':
                if self.linesDirs[ line ] == '0':
                    self.setLineDir( line, 1 )
            else:
                if self.linesDirs[ line ] == '1':
                    self.setLineDir( line, 0 )
                if self.linesModes[  line ] == 'pulse' \
                        and len( self.linesStates ) > line \
                        and self.getLineState( line ):
                    self.setLineState( line, False )

    def setLineStateCB( self, line, state, data, cb = None ):
        if data == 'OK':
            self.saveLineState( line, state )
        if cb:
            cb( data )

    def pulseLineCB( self, line, data, cb = None ):
        if data == 'OK':
            self.loop.call_later( 0.3, 
                    lambda: self.toggleLine( line, cb ) )
        elif cb:
            cb( data )
            
    def pulseLine( self, line, cb = None ):
        self.toggleLine( line, \
            lambda data: self.pulseLineCB( line, data, cb ) )

    def setCallback( self, line, callback ):
        lineNo = int( line )
        if not self.callbacks.has_key( lineNo ):
            self.callbacks[ lineNo ] = []
        self.callbacks[ lineNo ].append( callback )

    def getLineState( self, line ):
        if len( self.linesStates ) > int( line ):
            return self.linesStates[ int( line ) ] 
        else:
            return None

    def saveLinesDirs( self, data ):
        self.linesDirs = [ None ] + list( data )
        for line in self.linesModes.keys():
            self.checkLineMode( line )

    def saveLinesStates( self, data ):
        self.linesStates = [ None ] + [ x == '1' for x in list( data ) ]
        for line, mode in self.linesModes.items():
            if mode == 'pulse':
                self.checkLineMode( line )
        for line, callbacks in self.callbacks.items():
            for callback in callbacks:
                callback( self.linesStates[ line ] )


    def saveLineState( self, line, state ):
        if self.linesStates[ line ] != ( state == '1' ):
            self.linesStates[ line ] = ( state == '1' )
            #logging.info( "line " + str( line ) + ": " + str( state ) )
            if self.callbacks.get( line ):
                for callback in self.callbacks[ line ]:
                    callback( self.linesStates[ line ] )

    def connect( self ):

        def createProtocol():
            return ControllerProtocol( self )

        @asyncio.coroutine
        def _connect():
            while True:
                try:
                    yield from self.loop.create_connection( 
                            createProtocol, self.host, 2424 )
                except OSError as e:
                    logging.error( 'Error connecting to Jerome ' + self.host )
                else:
                    break
        if self.loop.is_running:
            asyncio.async( _connect() )
        else:
            self.loop.run_until_complete( _connect() )

    def __init__( self, loop, params ):
        self.loop = loop
        self.linesDirs = []
        self.linesModes = {}
        self.linesStates = []
        self.callbacks = {}
        self.setConnectedCallbacks = []
        self.pingTimer = None
        self.UARTconnection = None
        self.UARTenableRepeat = False
        self.UARTcache = []
        self.UARTtimer = None
        self.__connected = False
        if 'name' in params:
           self.name = params[ 'name' ]
        self.host = params[ 'host' ]
        self.UART = params[ 'UART' ]
        self.UARTdataCallbacks = []
        self.connect()

    def UARTconnect( self ):

        def createProtocol():
            return UARTProtocol( self )

        @asyncio.coroutine
        def _connect():
            while True:
                try:
                    yield from self.loop.create_connection( 
                            createProtocol, self.host, 2525 )
                except OSError as e:
                    logging.error( 'Error connecting to Jerome UART ' + self.host )
                else:
                    break
        if self.UART:
            if self.loop.is_running:
                asyncio.async( _connect() )
            else:
                self.loop.run_until_complete( _connect() )

    def UARTconnectionMade( self ):
        self.setUARTtimer()
#       self.UARTsend( 0 )
        logging.error( self.host + ' UART connected' )

    def UARTsend( self, data ):
        if self.UARTconnection:
            self.UARTcache = data
            logging.debug( 'UART send: ' + str( data ) )
            self.UARTconnection.transport.write( data )
            if self.UARTtimer and self.UARTtimer.active():
                self.UARTtimer.cancel()
            self.setUARTtimer()

    def setUARTtimer( self ):
        if self.UARTconnection and self.UARTinterval > 0:
            self.UARTtimer = self.loop.call_later( self.UARTinterval,
                lambda: self.UARTrepeat() )


    def UARTrepeat( self ):
        if self.UARTconnection and self.UARTenableRepeat:
            for d in self.UARTcache:
                self.UARTconnection.transport.write( chr( d ) )
            self.setUARTtimer()

    def UARTconnectionLost( self ):
        if self.UARTconnection:
            self.UARTconnection.transport.close()
            self.UARTconnection = None
        if self.UARTtimer and self.UARTtimer.active():
            self.UARTtimer.cancel()
        if self.connected and self.UART:
            self.UARTconnect()

    def UARTdataReceived( self, data ):
        data = data.replace( b'\x00', b'' )
        if data:
            logging.debug( 'UART ' + str( data ) )
            for cb in self.UARTdataCallbacks:
                cb( data )
       

    def getConnected( self ):
        return self.__connected

    def setConnected( self, val ):
        self.__connected = val
        for cb in self.setConnectedCallbacks:
            cb( val )
        if not val and self.UARTconnection:
            logging.error( "Controller " + self.host + \
                    ' UART disconnected' )
            self.UARTconnectionLost()
        if not val:
            self.connect()

    connected = property( getConnected, setConnected )




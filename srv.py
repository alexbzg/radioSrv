#!/usr/bin/python3
#coding=utf-8

import asyncio, logging
from aiohttp import web
from jeromeController import Controller

from common import siteConf, startLogging, loadJSON

conf = siteConf()
webRoot = conf.get( 'web', 'root' )
startLogging( 'srv' )

logging.debug( 'srv restart' )


wsConnections = []

trLine = conf.getint( 'encoders', 'trLine' )
trDelay = conf.getfloat( 'encoders', 'trDelay' )
answerTimeout = conf.getfloat( 'encoders', 'answerTimeout' )


async def testHandler(request):
    return web.Response( text = "OK" )

async def wsHandler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    logging.error( 'socket connected' )

    wsConnections.append( ws )
    for enc in encoders:
        await wsSend( ws, { 'encoders' : { enc: encData[enc]['val'] } } )
    await wsSend( ws, { 'controllerConnection': controller.connected } )

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            if msg.data == 'close':
                await ws.close()
        elif msg.type == aiohttp.WSMsgType.ERROR:
            logging.error('ws connection closed with exception %s' %
                  ws.exception())

    wsConnections.remove( ws )
    return ws

def setEncoderValue( enc, val ):
    encData[enc]['val'] = val
    asyncio.ensure_future( wsUpdate( { 'encoders': { enc: val } } ) )

async def wsSend( ws, data ):
    try:
        await ws.send_json( data )
        if ws.exception():
            logging.error( str( ws.exception() ) )
            wsConnections.remove( ws )
    except:
        logging.exception( 'ws send error' )
        wsConnections.remove( ws )

async def wsUpdate( data ):
    for ws in wsConnections:
        await wsSend( ws, data ) 

async def wsPing():
    logging.debug( 'ping started' )
    while True:
        for ws in wsConnections:
            try:
                await ws.ping()
                if ws.exception():
                    logging.error( str( ws.exception() ) )
                    wsConnections.remove( ws )
            except:
                logging.exception( 'ws send error' )
                wsConnections.remove( ws )
        await asyncio.sleep( 10 )


encoders = [ 1, 2 ]
encData = {}
curEncoder = 0
for enc in encoders:
    encData[enc] = { 'lo': -1, 'hi': -1, 'grey': -1, 'val': -1 }
encoderTimeoutTask = None

def UARTdataReceived(data):
    logging.debug( 'UART received: ' + str( data ) )
    global encoderTimeoutTask
    ed = encData[encoders[curEncoder]]
    for byte in data:
        if byte >= 128:
            ed['hi'] = ( byte - 128 ) << 5
        else:
            ed['lo'] = byte - 64
    if ed['lo'] != -1 and ed['hi'] != -1:
        if ed['grey'] != ed['lo'] + ed['hi']:
            ed['grey'] = ed['lo'] + ed['hi']
            val = ed['grey']
            mask = val >> 1
            while mask != 0:
                val = val ^ mask
                mask = mask >> 1
            logging.info( str( encoders[curEncoder] ) + ': ' + str(val) )
            setEncoderValue( encoders[curEncoder], val )
        if encoderTimeoutTask:
            encoderTimeoutTask.cancel()
            encoderTimeoutTask = None
            nextEncoder()

def controllerConnected( state ):
    global encoderTimeoutTask
    if state:
        global curEncoder 
        curEncoder = 0
        queryEncoders()
    else:
        if encoderTimeoutTask:
            encoderTimeoutTask.cancel()
            encoderTimeoutTask = None
    asyncio.ensure_future( wsUpdate( { 'controllerConnection': state } ) )

def nextEncoder():
    global curEncoder
    curEncoder += 1
    if curEncoder > len( encoders ) - 1:
        curEncoder = 0
    queryEncoders()

def onEncoderTimeout():
    logging.warning( 'answer timeout' )
    encoderTimeoutTask = None
    setEncoderValue( encoders[curEncoder], -1 )
    nextEncoder()

def queryEncoders():

    def sendQuery():
        global encoderTimeoutTask
        controller.UARTsend( bytes( [encoders[curEncoder]] ) )
        loop.call_later( trDelay, lambda: controller.setLineState( trLine, 0 ) )
        encoderTimeoutTask = loop.call_later( answerTimeout, onEncoderTimeout )

    controller.setLineState( trLine, 1 )
    loop.call_later( trDelay, sendQuery )

webApp = web.Application()
webApp.router.add_route('GET', '/aiohttp/test', testHandler )
webApp.router.add_route('GET', '/aiohttp/ws/encoders', wsHandler )

loop = asyncio.get_event_loop()
handler = webApp.make_handler()
f = loop.create_unix_server(handler, conf.get( 'web', 'socket' ) )
webSrv = loop.run_until_complete(f)

wsPingTask = asyncio.ensure_future( wsPing() )
#loop.run_until_complete( wsPingTask )

controller = Controller( loop, { 'host': conf.get( 'encoders', 'ip' ), 'UART': True } )
controller.setConnectedCallbacks.append( controllerConnected )
controller.setLineMode( trLine, 'out' )
controller.UARTdataCallbacks.append( UARTdataReceived )


try:
    loop.run_forever()
except KeyboardInterrupt:
    pass
except:
    logging.exception()
else:
    logging.error( 'Loop finished' )
finally:
#    loop.run_until_complete(handler.finish_connections(1.0))
    wsPingTask.cancel()
    webSrv.close()
    loop.run_until_complete(webSrv.wait_closed())
    loop.run_until_complete(webApp.finish())
loop.close()

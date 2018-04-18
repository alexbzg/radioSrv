#!/usr/bin/python3
#coding=utf-8

import asyncio, logging
from aiohttp import web
from jeromeController import Controller

wsConnections = []

async def testHandler(request):
    return web.Response( text = "OK" )

async def wsHandler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    logging.error( 'socket connected' )

    wsConnections.append( ws )
    for enc in encoders:
        await ws.send_json( { enc: encData[enc]['val'] } )

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            if msg.data == 'close':
                await ws.close()
        elif msg.type == aiohttp.WSMsgType.ERROR:
            logging.error('ws connection closed with exception %s' %
                  ws.exception())

    wsConnections.remove( ws )
    return ws

trLine = 13
trDelay = 0.02
answerTimeout = 0.25
encoders = [ 1 ]
encData = {}
curEncoder = 0
for enc in encoders:
    encData[enc] = { 'lo': -1, 'hi': -1, 'grey': -1, 'val': -1 }

async def UARTdataReceived(data):
    ed = encData[encoders[curEncoder]]
    for byte in data:
        if byte >= 128:
            ed['hi'] = ( byte - 128 ) << 5
        else:
            ed['lo'] = byte - 64
    if ( ed['lo'] != -1 and ed['hi'] != -1 and ed['grey'] != ed['lo'] + ed['hi'] ):
        ed['grey'] = ed['lo'] + ed['hi']
        ed['val'] = ed['grey']
        mask = ed['val'] >> 1
        while mask != 0:
            ed['val'] = ed['val'] ^ mask
            mask = mask >> 1
        logging.error( str( encoders[curEncoder] ) + ': ' + str(ed['val']) )
        for ws in wsConnections:
            await ws.send_json( { encoders[curEncoder]: ed['val'] } )

def controllerConnected( state ):
    if state:
        global curEncoder 
        curEncoder = 0
        queryEncoders()

def queryEncoders():

    def sendQuery():
        controller.UARTsend( bytes( [encoders[curEncoder]] ) )
        loop.call_later( trDelay, lambda: controller.setLineState( trLine, 0 ) )
        loop.call_later( answerTimeout, nextEnc )

    def nextEnc():
        global curEncoder
        curEncoder += 1
        if curEncoder > len( encoders ) - 1:
            curEncoder = 0
        queryEncoders()

    controller.setLineState( trLine, 1 )
    loop.call_later( trDelay, sendQuery )

webApp = web.Application()
webApp.router.add_route('GET', '/aiohttp/test', testHandler )
webApp.router.add_route('GET', '/aiohttp/ws', wsHandler )

loop = asyncio.get_event_loop()
handler = webApp.make_handler()
f = loop.create_unix_server(handler, '/tmp/radio.srv.dev.socket' )
webSrv = loop.run_until_complete(f)
controller = Controller( loop, { 'host': '192.168.0.101', 'UART': True } )
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
    loop.run_until_complete(handler.finish_connections(1.0))
    webSrv.close()
    loop.run_until_complete(webSrv.wait_closed())
    loop.run_until_complete(webApp.finish())
loop.close()

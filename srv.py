#!/usr/bin/python3
#coding=utf-8

import asyncio, logging, json, aiohttp
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

async def wsHandler(request):
    ws = web.WebSocketResponse( heartbeat = 0.5 )
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
            if msg.data == '__ping__':
                if ws in wsConnections:
                    await ws.send_str( '__pong__' )
                else:
                    await ws.close()
        elif msg.type == aiohttp.WSMsgType.ERROR:
            logging.error('ws connection closed with exception %s' %
                  ws.exception())

    wsRemove( ws )
    return ws

async def encSettingsHandler( request ):
    global encodersSettings
    data = await request.json()
    if 'controller' in data:
        encodersSettings['controller'] = data['controller']
        if data['controller']['host'] != controller.host:
            controller.close()
            startController()
    else:
        data['id'] = int( data['id'] )
        encodersSettings['encoders'] = [ x for x in encodersSettings['encoders'] if x['id'] != data['id'] ]
        if not 'delete' in data:
            encodersSettings['encoders'].append( data )
        sorted( encodersSettings['encoders'], key = lambda x: x['id'] )
        initEncData()
    with open( conf.get( 'web', 'root' ) + '/encoders.json', 'w' ) as f:
        json.dump( encodersSettings, f, ensure_ascii = False )
    await wsUpdate( { 'updateSettings': 1 } )
    curEncoder = 0
    for (k,v) in request.headers.items():
        logging.warning( k + ': ' + v )
    return web.Response( text = 'OK' )


def setEncoderValue( enc, val ):
    ed = encData[enc]
    ed['val'] = val
    ed['lo'] = -1
    ed['hi'] = -1
    if val != -1:
        ed['updated'] = True
    asyncio.ensure_future( wsUpdate( { 'encoders': { enc: val } } ) )

async def wsSend( ws, data ):
    if ws.closed:
        wsRemove( ws )
    else:
        try:
            await ws.send_json( data )
            if ws.exception():
                logging.error( str( ws.exception() ) )
                await ws.close()
                wsConnections.remove( ws )
        except:
            logging.exception( 'ws send error' )
            wsRemove( ws )

def wsRemove( ws ):
    if ws in wsConnections:
        wsConnections.remove( ws )

async def wsUpdate( data ):
    for ws in wsConnections:
        await wsSend( ws, data ) 

encodersSettings = loadJSON( conf.get( 'web', 'root' ) + '/encoders.json' )

encoders = []
encData = {}
curEncoder = -1

def initEncData():
    global encoders, encData
    encoders = []
    encData = {}
    for enc in encodersSettings['encoders']:
        encID = enc['id']
        encoders.append( encID )
        encData[encID] = { 'lo': -1, 'hi': -1, 'grey': -1, 'val': -1, 
                'updated': False }

initEncData()
encoderTimeoutTask = None

def UARTdataReceived(data):
    global encoderTimeoutTask    
    bts = []
    for b in data:
        bts.append(b)
    logging.debug( 'UART received: ' + str(bts) )
    if curEncoder == -1 or not encoderTimeoutTask:
        return
    ed = encData[encoders[curEncoder]]
    if len(data) > 1 and data[0] >= 128 and data[1] >= 64 and data[1] < 128:
        ed['hi'] = ( data[0] - 128 ) << 5
        ed['lo'] = data[1] - 64
        encoderTimeoutTask.cancel()
        logging.debug( str( curEncoder ) + ': timeout cancelled' )
        encoderTimeoutTask = None
        if ed['grey'] != ed['lo'] + ed['hi'] or ed['val'] == -1:
            ed['grey'] = ed['lo'] + ed['hi']
            val = ed['grey']
            mask = val >> 1
            while mask != 0:
                val = val ^ mask
                mask = mask >> 1
            logging.info( str( encoders[curEncoder] ) + ': ' + str(val) )
            if val < 1024:
                setEncoderValue( encoders[curEncoder], val )
        else:
            ed['updated'] = True
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
    global encoderTimeoutTask
    encoderTimeoutTask = None  
    if curEncoder <= len( encoders ) - 1:
        if not encData[encoders[curEncoder]]['updated']:
            logging.warning( str(encoders[curEncoder]) + ': answer timeout ')
            setEncoderValue( encoders[curEncoder], -1 )
    nextEncoder()

def queryEncoders():
    global encoderTimeoutTask

    def switchRec():
        logging.debug( str( curEncoder ) + ': switch REC' )
        controller.setLineState( trLine, 0 )
   
    def sendQuery():
        logging.debug( str( curEncoder ) + ': send query' )
        controller.UARTsend( bytes( [encoders[curEncoder]] ) )
        loop.call_later( trDelay, switchRec )

    if not encoderTimeoutTask:
        encoderTimeoutTask = loop.call_later( answerTimeout, onEncoderTimeout )
        logging.debug( str( curEncoder ) + ': timeout set' )
        encData[encoders[curEncoder]]['updated'] = False
        logging.debug( str( curEncoder ) + ': switch TR' )
        controller.setLineState( trLine, 1 )
        loop.call_later( trDelay, sendQuery )
    else:
        logging.exception( str(encoderTimeoutTask) )

webApp = web.Application()
webApp.router.add_get('/aiohttp/ws/encoders', wsHandler )
webApp.router.add_post('/aiohttp/encSettings', encSettingsHandler )

loop = asyncio.get_event_loop()
handler = webApp.make_handler()
webServers = []
if 'socket' in conf['web']:
    fs = loop.create_unix_server(handler, conf.get( 'web', 'socket' ) )
    loop.run_until_complete(fs)
    logging.error( 'listening to ' + conf['web']['socket'] )
if 'port' in conf['web']:
    fp = loop.create_server(handler, port = conf['web']['port'] )
    webSrv = loop.run_until_complete(fp)
    logging.error( 'listening to ' + conf['web']['port'] )

controller = None

def startController():
    global controller
    controller = Controller( loop, 
            { 'host': encodersSettings['controller']['host'], 'UART': True } )
    controller.setConnectedCallbacks.append( controllerConnected )
    controller.setLineMode( trLine, 'out' )
    controller.UARTdataCallbacks.append( UARTdataReceived )

startController()

try:
    logging.error( 'loop start' )
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
    for webSrv in webServers:
        webSrv.close()
        loop.run_until_complete(webSrv.wait_closed())
    loop.run_until_complete(webApp.finish())
loop.close()

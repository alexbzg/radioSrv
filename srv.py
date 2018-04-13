#!/usr/bin/python3
#coding=utf-8

import asyncio, logging
from aiohttp import web
from jeromeController import Controller

@asyncio.coroutine
def testHandler(request):
    return web.Response( text = "OK" )

webApp = web.Application()
webApp.router.add_route('GET', '/aiohttp/test', testHandler )

loop = asyncio.get_event_loop()
handler = webApp.make_handler()
f = loop.create_unix_server(handler, '/tmp/radio.srv.dev.socket' )
webSrv = loop.run_until_complete(f)
controller = Controller( loop, { 'host': '192.168.0.101', 'UART': True } )
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

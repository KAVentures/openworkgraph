"""Transport privacy is scoped to approved source reads, including concurrency."""
import asyncio
import logging

from server.source_http_logging import private_source_http_logs


def test_private_source_logs_do_not_mute_unrelated_async_requests(caplog):
    async def run():
        ready=asyncio.Event();other_done=asyncio.Event()
        async def source():
            with private_source_http_logs():
                logging.getLogger('httpx').info('PRIVATE_TICKET_URL')
                ready.set();await other_done.wait()
                logging.getLogger('httpcore.http11').debug('PRIVATE_PROVIDER_HEADERS')
        async def unrelated():
            await ready.wait();logging.getLogger('httpx').info('unrelated concurrent request preserved');other_done.set()
        await asyncio.gather(source(),unrelated())
    with caplog.at_level(logging.DEBUG):asyncio.run(run())
    assert 'PRIVATE_' not in caplog.text
    assert 'unrelated concurrent request preserved' in caplog.text


def test_nested_source_scope_and_exception_restore_normal_transport_logging(caplog):
    with caplog.at_level(logging.INFO):
        try:
            with private_source_http_logs():
                with private_source_http_logs():logging.getLogger('httpx').info('PRIVATE_INNER')
                logging.getLogger('httpx').info('PRIVATE_OUTER')
                raise ValueError('owned failure')
        except ValueError:pass
        logging.getLogger('httpx').info('ordinary request logging restored')
    assert 'PRIVATE_' not in caplog.text
    assert 'ordinary request logging restored' in caplog.text

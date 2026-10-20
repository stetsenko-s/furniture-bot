"""Самостоятельный сервис render: один процесс, собственный кэш и настройки."""
import os
from aiohttp import web
from infrastructure.env import data_dir
from infrastructure.stage_config import stage_settings
from infrastructure.stage_http import create_stage_app
from .provider import RenderProvider

def create_app(provider, directory, token):
    return create_stage_app('render', provider, directory, token)

def main():
    provider = RenderProvider(stage_settings('render'))
    web.run_app(create_app(provider, data_dir('render'), os.environ['RENDER_SERVICE_TOKEN']),
                host=os.environ.get('RENDER_HOST', '127.0.0.1'), port=int(os.environ.get('RENDER_PORT', '8094')), access_log=None)

if __name__ == '__main__':
    main()

"""Самостоятельный сервис dimensions: один процесс, собственный кэш и настройки."""
import os
from aiohttp import web
from infrastructure.env import data_dir
from infrastructure.stage_config import stage_settings
from infrastructure.stage_http import create_stage_app
from .provider import DimensionsProvider

def create_app(provider, directory, token):
    return create_stage_app('dimensions', provider, directory, token)

def main():
    provider = DimensionsProvider(stage_settings('dimensions'))
    web.run_app(create_app(provider, data_dir('dimensions'), os.environ['DIMENSIONS_SERVICE_TOKEN']),
                host=os.environ.get('DIMENSIONS_HOST', '127.0.0.1'), port=int(os.environ.get('DIMENSIONS_PORT', '8093')), access_log=None)

if __name__ == '__main__':
    main()

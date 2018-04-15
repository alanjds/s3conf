import os
import codecs
import logging
from io import StringIO
from tempfile import NamedTemporaryFile

import editor

from .storages import get_storage, strip_prefix
from .utils import prepare_path
from .config import Settings
from . import exceptions

logger = logging.getLogger(__name__)
__escape_decoder = codecs.getdecoder('unicode_escape')


def parse_dotenv(data):
    for line in data.splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')

            # Remove any leading and trailing spaces in key, value
            k, v = k.strip(), v.strip().encode('unicode-escape').decode('ascii')

            if v and v[0] == v[-1] in ['"', "'"]:
                v = __escape_decoder(v[1:-1])[0]

            yield k, v


def unpack_list(files_list):
    files_pairs = files_list.split(';')
    files_map = []
    for file_map in files_pairs:
        file_source, _, file_target = file_map.rpartition(':')
        if file_source and file_target:
            files_map.append((file_source, file_target))
    return files_map


def phusion_dump(environment, path):
    prepare_path(path if path.endswith('/') else path + '/')
    for k, v in environment.items():
        with open(os.path.join(path, k), 'w') as f:
            f.write(v + '\n')


def setup_environment(storage='s3',
                      dump=False,
                      dump_path='/etc/container_environment',
                      **kwargs):
    try:
        conf = S3Conf(storage=storage)
        env_vars = conf.get_variables(**kwargs)
        for var_name, var_value in env_vars.items():
            print('{}={}'.format(var_name, var_value))
        if dump:
            phusion_dump(env_vars, dump_path)
    except ValueError as e:
        logger.error(e)
    except Exception as e:
        logger.error(e)
        raise e


class S3Conf:
    def __init__(self, storage='s3', settings=None):
        self.settings = settings or Settings()
        self.storage = get_storage(storage, settings=self.settings)

    @property
    def environment_file_path(self):
        # resolving environment file path
        file_name = self.settings.get('S3CONF')
        if not file_name:
            logger.error('Environemnt file name is not defined or is empty.')
            raise exceptions.EnvfilePathNotDefinedError()
        return file_name

    def map_files(self, files, root_dir=None):
        if isinstance(files, str):
            files = unpack_list(files)
        for file_source, file_target in files:
            self.download(file_source, file_target, root_dir=root_dir)

    def download(self, path, path_target, root_dir=None):
        if root_dir:
            path_target = os.path.join(root_dir, path_target.lstrip('/'))
        logger.info('Downloading %s to %s', path, path_target)
        for file_path in self.storage.list(path):
            if path.endswith('/') or not path:
                target_name = os.path.join(path_target, file_path)
            else:
                target_name = path_target
            prepare_path(target_name)
            with open(target_name, 'wb') as f:
                # join might add a trailing slash, but we know it is a file, so we remove it
                # stream=f reads the data into f and returns f as our open file
                self.storage.open(os.path.join(path, file_path).rstrip('/'), stream=f)

    def upload(self, path, path_target, root_dir=None):
        if root_dir:
            path = os.path.join(root_dir, path.lstrip('/'))
        logger.info('Uploading %s to %s', path, path_target)
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for file in files:
                    file_source = os.path.join(root, file)
                    file_target = os.path.join(path_target, strip_prefix(os.path.join(root, file), path).lstrip('/'))
                    self.storage.write(open(file_source, 'rb'), file_target)
        else:
            self.storage.write(open(path, 'rb'), path_target)

    def get_variables(self):
        logger.info('Loading configs from {}'.format(self.environment_file_path))
        file_data = str(self.storage.open(self.environment_file_path).read(), 'utf-8')
        return dict(parse_dotenv(file_data))

    def edit(self):
        with NamedTemporaryFile(mode='rb+', buffering=0) as f:
            original_data = self.storage.open(self.environment_file_path).read()
            f.write(original_data)
            edited_data = editor.edit(filename=f.name)
            if edited_data != original_data:
                self.upload(f.name, self.environment_file_path)
            else:
                logger.warning('File not changed. Nothing to upload.')

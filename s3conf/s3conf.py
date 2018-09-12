import os
import codecs
import logging

from .utils import prepare_path, md5s3
from .config import Settings
from . import exceptions, files, storages

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


def change_root_dir(file_path, root_dir=None):
    if root_dir:
        file_path = os.path.join(root_dir, file_path.lstrip('/'))
    return file_path


class S3Conf:
    def __init__(self, storage=None, settings=None):
        self.settings = settings or Settings()
        self.storage = storage or storages.S3Storage(settings=self.settings)

    @property
    def environment_file_path(self):
        # resolving environment file path
        file_name = self.settings.get('S3CONF')
        if not file_name:
            logger.error('Environemnt file name is not defined or is empty.')
            raise exceptions.EnvfilePathNotDefinedError()
        return file_name

    def downsync(self, files, root_dir=None):
        if isinstance(files, str):
            files = unpack_list(files)
        for remote_file, local_file in files:
            self.download(remote_file, change_root_dir(local_file, root_dir))

    def upsync(self, files, root_dir=None):
        if isinstance(files, str):
            files = unpack_list(files)
        for remote_file, local_file in files:
            self.upload(change_root_dir(local_file, root_dir), remote_file)

    def download(self, path, path_target, force=False):
        hashes = {}
        logger.info('Downloading %s to %s', path, path_target)
        for md5hash, file_path in self.storage.list(path):
            if path.endswith('/') or not path:
                target_name = os.path.join(path_target, file_path)
            else:
                target_name = path_target
            prepare_path(target_name)
            existing_md5 = md5s3(open(target_name, 'rb')) if os.path.exists(target_name) and not force else None
            if not existing_md5 or existing_md5 != md5hash:
                source_name = os.path.join(path, file_path).rstrip('/')
                logger.debug('Transferring file %s to %s', source_name, target_name)
                with open(target_name, 'wb') as f:
                    # join might add a trailing slash, but we know it is a file, so we remove it
                    self.storage.open(source_name).read_into_stream(f)
            hashes[file_path] = md5hash
        return hashes

    def upload(self, path, path_target):
        logger.info('Uploading %s to %s', path, path_target)
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for file in files:
                    file_source = os.path.join(root, file)
                    file_target = os.path.join(path_target,
                                               storages.strip_prefix(os.path.join(root, file), path).lstrip('/'))
                    self.storage.write(open(file_source, 'rb'), file_target)
        else:
            self.storage.write(open(path, 'rb'), path_target)

    def get_envfile(self):
        logger.info('Loading configs from {}'.format(self.environment_file_path))
        return files.EnvFile.from_file(self.storage.open(self.environment_file_path))

    def edit(self, create=False):
        files.EnvFile.from_file(self.storage.open(self.environment_file_path)).edit(create=create)

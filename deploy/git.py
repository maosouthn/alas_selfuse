from deploy.config import DeployConfig, ExecutionError
from deploy.git_over_cdn.client import GitOverCdnClient
from deploy.logger import logger
from deploy.utils import *


class GitManager(DeployConfig):
    @cached_property
    def git(self):
        exe = self.filepath('GitExecutable')
        if os.path.exists(exe):
            return exe

        logger.warning(f'GitExecutable: {exe} does not exist, use `git` instead')
        return 'git'

    @staticmethod
    def remove(file):
        try:
            os.remove(file)
            logger.info(f'Removed file: {file}')
        except FileNotFoundError:
            logger.info(f'File not found: {file}')

    def git_repository_init(
            self, repo, source='origin', branch='master', proxy='', ssl_verify=True
    ):
        logger.hr('Git Init', 1)
        if not self.execute(f'"{self.git}" init', allow_failure=True):
            self.remove('./.git/config')
            self.remove('./.git/index')
            self.remove('./.git/HEAD')
            self.execute(f'"{self.git}" init')

        logger.hr('Set Git Proxy', 1)
        if proxy:
            self.execute(f'"{self.git}" config --local http.proxy {proxy}')
            self.execute(f'"{self.git}" config --local https.proxy {proxy}')
        else:
            self.execute(f'"{self.git}" config --local --unset http.proxy', allow_failure=True)
            self.execute(f'"{self.git}" config --local --unset https.proxy', allow_failure=True)

        if ssl_verify:
            self.execute(f'"{self.git}" config --local http.sslVerify true', allow_failure=True)
        else:
            self.execute(f'"{self.git}" config --local http.sslVerify false', allow_failure=True)

        logger.hr('Set Git Repository', 1)
        if not self.execute(f'"{self.git}" remote set-url {source} {repo}', allow_failure=True):
            self.execute(f'"{self.git}" remote add {source} {repo}')

        logger.hr('Fetch Repository Branch', 1)
        # Fetch the upstream master branch, not the local working branch.
        # `branch` is the local working branch (e.g. `hazard1-tweak`); upstream ALAS
        # only has `master`, so fetching `{branch}` here would fail.
        self.execute(f'"{self.git}" fetch {source} master')

        logger.hr('Pull Repository Branch', 1)
        # Remove git lock
        for lock_file in [
            './.git/index.lock',
            './.git/HEAD.lock',
            './.git/refs/heads/master.lock',
        ]:
            if os.path.exists(lock_file):
                logger.info(f'Lock file {lock_file} exists, removing')
                os.remove(lock_file)
        # Merge upstream master into the working branch, so local modifications are kept.
        # (The old `git reset --hard` deleted all local modifications.)
        if not self.execute(f'"{self.git}" rev-parse --verify HEAD', allow_failure=True):
            # Fresh install: no local commits yet, create the working branch from upstream
            self.execute(f'"{self.git}" checkout -b {branch} {source}/master')
        elif not self.execute(f'"{self.git}" merge {source}/master --no-edit', allow_failure=True):
            logger.hr('Merge conflict detected', 0)
            logger.info('Please resolve the conflict manually:')
            logger.info('  git status            # list conflicted files')
            logger.info('  # edit the files to keep both sides, remove <<<<<<< ======= >>>>>>> marks')
            logger.info('  git add . && git commit -m "Resolve merge conflict"')
            logger.info('Then re-open Alas, and update again.')
            raise ExecutionError
        # Regenerate and commit generated config files, in case upstream changed arguments
        self.execute(
            f'"{self.filepath("PythonExecutable")}" -m module.config.config_updater',
            allow_failure=True,
        )
        self.execute(
            f'"{self.git}" add config/template.json module/config/config_generated.py '
            f'module/config/argument/args.json module/config/argument/menu.json module/config/i18n/',
            allow_failure=True,
        )
        self.execute(f'"{self.git}" commit -m "Update generated config"', allow_failure=True)

        logger.hr('Show Version', 1)
        self.execute(f'"{self.git}" --no-pager log --no-merges -1')

    @property
    def goc_client(self):
        client = GitOverCdnClient(
            url=[
                'https://1818706573.cdn.123clouddisk.com/1818706573/pack/LmeSzinc_AzurLaneAutoScript_master',
                'https://vip.123pan.cn/1818706573/pack/LmeSzinc_AzurLaneAutoScript_master',
            ],
            folder=self.root_filepath,
            source='origin',
            branch='master',
            git=self.git,
        )
        client.logger = logger
        return client

    def git_install(self):
        logger.hr('Update Alas', 0)

        if not self.AutoUpdate:
            logger.info('AutoUpdate is disabled, skip')
            return

        if self.GitOverCdn:
            if self.goc_client.update():
                return

        self.git_repository_init(
            repo=self.Repository,
            source='origin',
            branch=self.Branch,
            proxy=self.GitProxy,
            ssl_verify=self.SSLVerify,
        )


if __name__ == '__main__':
    self = GitManager()
    self.goc_client.get_status()

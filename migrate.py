#!/usr/bin/env python

from __future__ import print_function

import argparse
import errno
import os
import os.path
import pickle
import subprocess
import sys


def verify_initial(prefix):
    if not os.path.isdir(prefix):
        print('%s does not exist! wtf?!' % (prefix,))
        raise SystemExit(1)

    lib64 = os.path.join(prefix, 'lib64')
    lib32 = os.path.join(prefix, 'lib32')
    lib = os.path.join(prefix, 'lib')
    lib_new = os.path.join(prefix, 'lib.new')

    if not os.path.isdir(lib64) and not os.path.islink(lib64):
        print('%s needs to exist as a real directory!' % (lib64,), file=sys.stderr)
        raise SystemExit(1)

    if os.path.islink(lib32):
        print('%s is a symlink! was the migration done already?' % (lib32,), file=sys.stderr)
        raise SystemExit(1)

    if os.path.isdir(lib) and not os.path.islink(lib):
        print('%s is a real directory! was the migration done already?' % (lib,), file=sys.stderr)
        raise SystemExit(1)

    if os.path.islink(lib) and os.readlink(lib) == 'lib.new':
        print('%s is a symlink to lib.new! did you want to --finish?' % (lib,), file=sys.stderr)
        raise SystemExit(1)

    if not os.path.islink(lib) or os.readlink(lib) != 'lib64':
        print('%s needs to be a symlink to lib64!' % (lib,), file=sys.stderr)
        raise SystemExit(1)

    if os.path.isdir(lib_new):
        print('%s exists! do you need to remove failed migration?' % (lib_new,), file=sys.stderr)
        raise SystemExit(1)


def verify_migrated(prefix):
    if not os.path.isdir(prefix):
        print('%s does not exist! wtf?!' % (prefix,))
        raise SystemExit(1)

    lib64 = os.path.join(prefix, 'lib64')
    lib32 = os.path.join(prefix, 'lib32')
    lib = os.path.join(prefix, 'lib')
    lib_new = os.path.join(prefix, 'lib.new')

    if not os.path.isdir(lib64) and not os.path.islink(lib64):
        print('%s needs to exist as a real directory!' % (lib64,), file=sys.stderr)
        raise SystemExit(1)

    if os.path.islink(lib32):
        print('%s is a symlink! was the migration finished already?' % (lib32,), file=sys.stderr)
        raise SystemExit(1)

    if os.path.isdir(lib) and not os.path.islink(lib):
        print('%s is a real directory! was the migration finished already?' % (lib,), file=sys.stderr)
        raise SystemExit(1)

    if os.path.islink(lib) and os.readlink(lib) == 'lib64':
        print('%s is a symlink to lib64! did the migration succeed?' % (lib,), file=sys.stderr)
        raise SystemExit(1)

    if not os.path.isdir(lib_new):
        print('%s does not exist! did you --migrate?' % (lib_new,), file=sys.stderr)
        raise SystemExit(1)

    if not os.path.islink(lib) or os.readlink(lib) != 'lib.new':
        print('%s needs to be a symlink to lib.new!' % (lib,), file=sys.stderr)
        raise SystemExit(1)


def path_get_leftmost_dirs(paths):
    for p in paths:
        if '/' in p:
            yield p.split('/', 1)[0]


def path_get_top_files(paths):
    for p in paths:
        if '/' not in p:
            yield p


def nonfatal_remove(fp):
    try:
        os.remove(fp)
    except OSError as e:
        if e.errno in (errno.EISDIR, errno.EPERM):
            try:
                os.rmdir(fp)
            except OSError as e:
                if e.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    print('Removing %s failed: %s' % (fp, e))
        else:
            print('Removing %s failed: %s' % (fp, e))


class MigrationState(object):
    __slots__ = ('eprefix', 'excludes', 'includes', 'prefixes')

    def __init__(self, eprefix):
        self.eprefix = eprefix

    def analyze(self):
        from portage import create_trees

        print('Analyzing files installed into lib & lib64...', file=sys.stderr)

        subprefixes = (
            self.eprefix,
            os.path.join(self.eprefix, 'usr'),
        )
        self.prefixes = subprefixes

        lib_path = dict((prefix, os.path.join(prefix, 'lib/')) for prefix in subprefixes)
        lib32_path = dict((prefix, os.path.join(prefix, 'lib32/')) for prefix in subprefixes)
        lib64_path = dict((prefix, os.path.join(prefix, 'lib64/')) for prefix in subprefixes)

        lib_paths = dict((prefix, set()) for prefix in subprefixes)
        lib32_paths = dict((prefix, set()) for prefix in subprefixes)
        lib64_paths = dict((prefix, set()) for prefix in subprefixes)

        trees = create_trees()
        vardb = trees[max(trees)]['vartree'].dbapi
        for p in vardb.cpv_all():
            for f, details in vardb._dblink(p).getcontents().items():
                # skip directories; we will get them implicitly via
                # files contained within them
                if details[0] == 'dir':
                    continue
                for prefix in subprefixes:
                    if f.startswith(lib_path[prefix]):
                        lib_paths[prefix].add(f[len(lib_path[prefix]):])
                        break
                    if f.startswith(lib32_path[prefix]):
                        lib32_paths[prefix].add(f[len(lib32_path[prefix]):])
                        break
                    if f.startswith(lib64_path[prefix]):
                        lib64_paths[prefix].add(f[len(lib64_path[prefix]):])
                        break

        pure_lib = {}
        mixed_lib = {}

        self.excludes = {}
        self.includes = {}

        for prefix in subprefixes:
            print('', file=sys.stderr)
            lib_paths[prefix] = frozenset(lib_paths[prefix])
            lib32_paths[prefix] = frozenset(lib32_paths[prefix])
            lib64_paths[prefix] = frozenset(lib64_paths[prefix])

            lib_prefixes = frozenset(path_get_leftmost_dirs(lib_paths[prefix]))
            lib64_prefixes = frozenset(path_get_leftmost_dirs(lib64_paths[prefix]))
            lib_files = frozenset(path_get_top_files(lib_paths[prefix]))
            lib64_files = frozenset(path_get_top_files(lib64_paths[prefix]))

            pure_lib = lib_prefixes - lib64_prefixes
            mixed_lib = lib_prefixes & lib64_prefixes

            unowned_files = (frozenset(os.listdir(lib_path[prefix]))
                             - lib_prefixes - lib64_prefixes
                             - lib_files - lib64_files)
            # library symlinks go to lib64
            lib64_unowned = frozenset(x for x in unowned_files
                                      if os.path.splitext(x)[1]
                                      in ('.a', '.la', '.so'))
            lib_unowned = unowned_files - lib64_unowned

            print('pure %s:' % (lib_path[prefix],), file=sys.stderr)
            for p in pure_lib:
                print('\t%s' % (p,), file=sys.stderr)
            print('\t(+ %d files)' % (len(lib_files),), file=sys.stderr)
            print('', file=sys.stderr)

            print('split %s+%s:' % (lib_path[prefix], lib64_path[prefix]), file=sys.stderr)
            for p in mixed_lib:
                print('\t%s' % (p,), file=sys.stderr)

            if lib_unowned:
                print('', file=sys.stderr)
                print('unowned files for %s:' % (lib_path[prefix],), file=sys.stderr)
                for p in lib_unowned:
                    print('\t%s' % (p,), file=sys.stderr)

            if lib64_unowned:
                print('', file=sys.stderr)
                print('unowned files for %s:' % (lib64_path[prefix],), file=sys.stderr)
                for p in lib64_unowned:
                    print('\t%s' % (p,), file=sys.stderr)

            # prepare the exclude lists
            excludes = set()
            for p in lib64_paths[prefix]:
                for tp in mixed_lib:
                    if p.startswith(tp):
                        excludes.add(p)
                        break

            # store the data
            self.includes[prefix] = lib_prefixes | lib_files | lib_unowned
            self.excludes[prefix] = frozenset(excludes)

            # verify for conflicts
            conflicts = lib_paths[prefix] & lib32_paths[prefix]
            if conflicts:
                print('', file=sys.stderr)
                print('One or more files are both in %s&%s, making the conversion impossible.' % (
                    lib_path[prefix], lib32_path[prefix]), file=sys.stderr)
                print('', file=sys.stderr)
                for p in sorted(conflicts):
                    print('\t%s' % (p,), file=sys.stderr)
                print('', file=sys.stderr)
                print('Please report a bug at https://bugs.gentoo.org/, and do not proceed with', file=sys.stderr)
                print('the migration until a proper solution is found.', file=sys.stderr)
                raise SystemExit(1)

            conflicts = lib_paths[prefix] & lib64_paths[prefix]
            if conflicts:
                print('', file=sys.stderr)
                print('One or more files are both in %s&%s, making the conversion impossible.' % (
                    lib_path[prefix], lib64_path[prefix]), file=sys.stderr)
                print('', file=sys.stderr)
                for p in sorted(conflicts):
                    print('\t%s' % (p,), file=sys.stderr)
                print('', file=sys.stderr)
                print('Please report a bug at https://bugs.gentoo.org/, and do not proceed with', file=sys.stderr)
                print('the migration until a proper solution is found.', file=sys.stderr)
                raise SystemExit(1)

    def migrate(self):
        # create the lib.new directories
        for prefix in self.prefixes:
            lib = os.path.join(prefix, 'lib')
            lib32 = os.path.join(prefix, 'lib32')
            lib_new = os.path.join(prefix, 'lib.new')

            os.mkdir(lib_new)

            print('%s & %s -> %s ...' % (lib32, lib, lib_new), file=sys.stderr)
            cmd = ['cp', '-a', '--reflink=auto', '--',
                   os.path.join(lib32, '.')]
            # include all appropriate pure&mixed lib stuff
            for p in self.includes[prefix]:
                assert not p.endswith('/')
                cmd.append(os.path.join(lib, p))
            cmd.append(lib_new + '/')

            p = subprocess.Popen(cmd)
            if p.wait() != 0:
                print('Non-successful return from cp: %d' % (p.returncode,), file=sys.stderr)
                raise SystemExit(1)

            if self.excludes[prefix]:
                print('Remove extraneous files from %s ...' % (lib_new,), file=sys.stderr)
                # remove excluded stuff
                for p in self.excludes[prefix]:
                    fp = os.path.join(lib_new, p)
                    os.unlink(fp)
                    try:
                        os.removedirs(os.path.dirname(fp))
                    except OSError as e:
                        if e.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                            raise

        for prefix in self.prefixes:
            lib = os.path.join(prefix, 'lib')
            lib_tmp = os.path.join(prefix, 'lib.tmp')
            print('Updating: %s -> lib.new ...' % (lib,), file=sys.stderr)
            os.symlink('lib.new', lib_tmp)
            os.rename(lib_tmp, lib)

    def rollback(self):
        # restore the old 'lib' symlink
        for prefix in self.prefixes:
            lib = os.path.join(prefix, 'lib')
            lib_tmp = os.path.join(prefix, 'lib.tmp')
            print('Updating: %s -> lib64 ...' % (lib,), file=sys.stderr)
            os.symlink('lib64', lib_tmp)
            os.rename(lib_tmp, lib)

        # clean up lib.new
        for prefix in self.prefixes:
            lib_new = os.path.join(prefix, 'lib.new')
            print('Removing: %s ...' % (lib_new,))
            subprocess.Popen(['rm', '-rf', '--', lib_new]).wait()

    def finish(self):
        # replace the 'lib' symlink with the directory
        for prefix in self.prefixes:
            lib = os.path.join(prefix, 'lib')
            lib_new = os.path.join(prefix, 'lib.new')
            print('Renaming %s -> %s ...' % (lib_new, lib), file=sys.stderr)
            os.unlink(lib)
            os.rename(lib_new, lib)

        # replace 'lib32' with a symlink
        for prefix in self.prefixes:
            lib32 = os.path.join(prefix, 'lib32')
            print('Removing: %s ...' % (lib32,), file=sys.stderr)
            if subprocess.Popen(['rm', '-rf', '--', lib32]).wait() != 0:
                print('Removal failed for %s, you need to remove it manually and replace', file=sys.stderr)
                print('with a symlink to lib.', file=sys.stderr)
            else:
                print('Updating: %s -> lib ...' % (lib32,), file=sys.stderr)
                try:
                    os.symlink('lib', lib32)
                except OSError:
                    print('Symlinking failed for %s, please symlink it manually to lib.', file=sys.stderr)

        # clean up extraneous files from 'lib64'
        for prefix in self.prefixes:
            lib64 = os.path.join(prefix, 'lib64')
            print('Removing stale files from %s ...' % (lib64,))
            for p in self.includes[prefix]:
                for root, dirs, files in os.walk(os.path.join(lib64, p), topdown=False):
                    for f in dirs + files:
                        fp = os.path.join(root, f)
                        rp = os.path.relpath(fp, lib64)
                        if rp not in self.excludes[prefix]:
                            nonfatal_remove(fp)
                nonfatal_remove(os.path.join(lib64, p))

    def save_state(self):
        with open(os.path.expanduser('~/.symlink_lib_migrate.state'), 'wb') as f:
            pickle.dump((self.prefixes, self.excludes, self.includes), f)

    def load_state(self):
        try:
            with open(os.path.expanduser('~/.symlink_lib_migrate.state'), 'rb') as f:
                self.prefixes, self.excludes, self.includes = pickle.load(f)
        except (OSError, IOError) as e:
            if e.errno == errno.ENOENT:
                return False
            else:
                raise
        return True

    def clear_state(self):
        try:
            os.unlink(os.path.expanduser('~/.symlink_lib_migrate.state'))
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise


def main():
    argp = argparse.ArgumentParser()
    argp.add_argument('--analyze', action='store_const', dest='action',
                      const='analyze', help='Analyze and store system state',
                      default='analyze')
    argp.add_argument('--migrate', action='store_const', dest='action',
                      const='migrate', help='Perform the migration')
    argp.add_argument('--rollback', action='store_const', dest='action',
                      const='rollback', help='Revert the migration (after --migrate)')
    argp.add_argument('--finish', action='store_const', dest='action',
                      const='finish', help='Finish the migration (clean up)')
    args = argp.parse_args()

    is_root = os.geteuid() == 0

    if not is_root:
        if args.action != 'analyze':
            argp.error('Requested action requires root privileges')
        else:
            print('[Running as unprivileged user, results will not be saved]',
                  file=sys.stderr)

    if args.action == 'analyze':
        verify_initial('/')
        verify_initial('/usr')

        m = MigrationState('/')
        m.analyze()
        if is_root:
            m.save_state()
            print('', file=sys.stderr)
            print('The state has been saved and the migration is ready to proceed.', file=sys.stderr)
            print('To initiate it, please run:', file=sys.stderr)
            print('', file=sys.stderr)
            print('\t%s --migrate' % (sys.argv[0],), file=sys.stderr)
            print('', file=sys.stderr)
            print('Please do not perform any changes to the system at this point.', file=sys.stderr)
            print('If you performed any changes, please rerun the analysis.', file=sys.stderr)
        else:
            print('', file=sys.stderr)
            print('Everything looks good from here. However, you need to rerun', file=sys.stderr)
            print('the process as root to confirm.', file=sys.stderr)
    elif args.action == 'migrate':
        verify_initial('/')
        verify_initial('/usr')

        m = MigrationState('/')
        if not m.load_state():
            print('State file could not be loaded. Did you run --analyze?', file=sys.stderr)
        m.migrate()
        print('', file=sys.stderr)
        print('Initial migration complete. Please now test whether your system works', file=sys.stderr)
        print('correctly. It might be a good idea to try rebooting it. Once tested,', file=sys.stderr)
        print('complete the migration and clean up backup files via calling:', file=sys.stderr)
        print('', file=sys.stderr)
        print('\t%s --finish' % (sys.argv[0],), file=sys.stderr)
        print('', file=sys.stderr)
        print('If you wish to revert the changes, run:', file=sys.stderr)
        print('', file=sys.stderr)
        print('\t%s --rollback' % (sys.argv[0],), file=sys.stderr)
    elif args.action == 'rollback':
        verify_migrated('/')
        verify_migrated('/usr')

        m = MigrationState('/')
        if not m.load_state():
            print('State file could not be loaded. Did you run --analyze?', file=sys.stderr)
        m.rollback()
        m.clear_state()
        print('', file=sys.stderr)
        print('Rollback complete. Your system should now be as before the migration.', file=sys.stderr)
        print('Please look into fixing your issues and try again.', file=sys.stderr)
    elif args.action == 'finish':
        verify_migrated('/')
        verify_migrated('/usr')

        m = MigrationState('/')
        if not m.load_state():
            print('State file could not be loaded. Did you run --analyze?', file=sys.stderr)
        m.finish()
        m.clear_state()
        print('', file=sys.stderr)
        print('Migration complete. Please switch to the new profiles, or add', file=sys.stderr)
        print('the following to your make.conf (or equivalent):', file=sys.stderr)
        print('', file=sys.stderr)
        print('\tSYMLINK_LIB=no', file=sys.stderr)
        print('\tLIBDIR_x86=lib', file=sys.stderr)
        print('', file=sys.stderr)
        print('Afterwards, please rebuild all installed GCC versions and all', file=sys.stderr)
        print('packages installing into lib32, e.g.:', file=sys.stderr)
        print('', file=sys.stderr)
        print('\temerge -1v /usr/lib/gcc /lib32 /usr/lib32', file=sys.stderr)
        print('', file=sys.stderr)
        print('When the rebuilds are complete, the package manager should remove', file=sys.stderr)
        print('the lib32 symlink. If it does not, do:', file=sys.stderr)
        print('', file=sys.stderr)
        print('\trm /lib32 /usr/lib32', file=sys.stderr)
    else:
        raise NotImplementedError()


if __name__ == '__main__':
    main()

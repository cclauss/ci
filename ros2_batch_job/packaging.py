# Copyright 2015 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import glob
import os
import platform
import shutil
import sys
import tarfile
import zipfile

from .util import info


def build_and_test_and_package(args, job):
    print('# BEGIN SUBSECTION: build')
    # ignore ROS 1 bridge package for now
    ros1_bridge_path = os.path.join(args.sourcespace, 'ros2', 'ros1_bridge')
    info('ROS1 bridge path: %s' % ros1_bridge_path)
    ros1_bridge_ignore_marker = None
    if os.path.exists(ros1_bridge_path):
        ros1_bridge_ignore_marker = os.path.join(ros1_bridge_path, 'COLCON_IGNORE')
        info('ROS1 bridge path ignore file: %s' % ros1_bridge_ignore_marker)
        with open(ros1_bridge_ignore_marker, 'w'):
            pass

    # Now run build
    cmd = [
        args.colcon_script, 'build',
        '--base-paths', '"%s"' % args.sourcespace,
        '--build-base', '"%s"' % args.buildspace,
        '--install-base', '"%s"' % args.installspace,
    ] + (['--merge-install'] if not args.isolated else []) + \
        args.build_args

    cmake_args = ['-DBUILD_TESTING=OFF', '--no-warn-unused-cli']
    if args.cmake_build_type:
        cmake_args.append(
            '-DCMAKE_BUILD_TYPE=' + args.cmake_build_type)
    if '--cmake-args' in cmd:
        index = cmd.index('--cmake-args')
        cmd[index + 1:index + 1] = cmake_args
    else:
        cmd.append('--cmake-args')
        cmd.extend(cmake_args)

    job.run(cmd)

    if ros1_bridge_ignore_marker:
        os.remove(ros1_bridge_ignore_marker)
    print('# END SUBSECTION')

    # It only makes sense to build the bridge for Linux or OSX, since
    # ROS1 is not supported on Windows
    if args.os in ['linux', 'osx']:
        print('# BEGIN SUBSECTION: build ROS 1 bridge')
        # Now run build only for the bridge
        env = dict(os.environ)
        env['MAKEFLAGS'] = '-j1'
        job.run([
            args.colcon_script, 'build',
            '--base-paths', '"%s"' % args.sourcespace,
            '--build-base', '"%s"' % args.buildspace,
            '--install-base', '"%s"' % args.installspace,
            '--cmake-args', '-DBUILD_TESTING=ON', '--no-warn-unused-cli',
            '--packages-select', 'ros1_bridge',
            '--event-handlers', 'console_direct+',
        ] + (['--merge-install'] if not args.isolated else []) +
            (
                ['--cmake-args', '-DCMAKE_BUILD_TYPE=' +
                    args.cmake_build_type]
                if args.cmake_build_type else []
        ), env=env)
        print('# END SUBSECTION')

        if args.test_bridge:
            print('# BEGIN SUBSECTION: install Python packages for ros1_bridge test')
            job.run(['"%s"' % job.python, '-m', 'pip', 'install', '-U', 'catkin_pkg', 'rospkg'], shell=True)
            print('# END SUBSECTION')

            print('# BEGIN SUBSECTION: test ROS 1 bridge')
            # Now run test only for the bridge
            ret_test = job.run([
                args.colcon_script, 'test',
                '--base-paths', '"%s"' % args.sourcespace,
                '--build-base', '"%s"' % args.buildspace,
                '--install-base', '"%s"' % args.installspace,
                '--packages-select', 'ros1_bridge',
                # Ignore return codes that indicate test failures;
                # they'll be picked up later in test reporting.
                # '--ignore-return-codes',
            ] + (['--merge-install'] if not args.isolated else []) +
                args.test_args, exit_on_error=False, shell=True)
            info("test returned: '{0}'".format(ret_test))
            print('# END SUBSECTION')
            if ret_test:
                return ret_test

            print('# BEGIN SUBSECTION: test-result')
            # Collect the test results
            ret_test_results = job.run([
                args.colcon_script, 'test-result',
                '--test-result-base', '"%s"' % args.buildspace],
                exit_on_error=False, shell=True
            )
            info("test-result returned: '{0}'".format(ret_test_results))
            print('# END SUBSECTION')

    # Only on Linux and OSX Python scripts have a shebang line
    if args.os in ['linux', 'osx']:
        print('# BEGIN SUBSECTION: rewrite shebang lines')
        paths_to_files = []

        bin_path = os.path.join(args.installspace, 'bin')
        bin_contents = [os.path.join(bin_path, name) for name in os.listdir(bin_path)]
        paths_to_files.extend([path for path in bin_contents if os.path.isfile(path)])

        # Demo nodes are installed to 'lib/<package_name>'
        lib_path = os.path.join(args.installspace, 'lib')
        for lib_sub_dir in next(os.walk(lib_path))[1]:
            sub_dir_path = os.path.join(lib_path, lib_sub_dir)
            sub_dir_contents = [os.path.join(sub_dir_path, name) for name in os.listdir(sub_dir_path)]
            paths_to_files.extend([path for path in sub_dir_contents if os.path.isfile(path)])

        for path in paths_to_files:
            with open(path, 'rb') as h:
                content = h.read()
            shebang = b'#!%b' % job.python.encode()
            if content[0:len(shebang)] != shebang:
                continue
            print('- %s' % path)
            if args.os == 'osx':
                new_shebang = b'#!/usr/local/bin/python3'
            else:
                # in the linux case
                new_shebang = b'#!/usr/bin/env python3'
            with open(path, 'wb') as h:
                h.write(new_shebang)
                h.write(content[len(shebang):])
        print('# END SUBSECTION')

    print('# BEGIN SUBSECTION: create archive')
    # Remove "unnecessary" executables
    ros1_bridge_libexec_path = os.path.join(args.installspace, 'lib', 'ros1_bridge')
    if os.path.isdir(ros1_bridge_libexec_path):
        for filename in os.listdir(ros1_bridge_libexec_path):
            if (
                filename.startswith('simple_bridge') or
                filename.startswith('static_bridge') or
                filename.startswith('test_')
            ):
                os.remove(os.path.join(ros1_bridge_libexec_path, filename))

    # create an archive
    folder_name = 'ros2-' + args.os
    if args.os == 'linux' or args.os == 'osx':
        archive_path = 'ros2-package-%s-%s.tar.bz2' % (args.os, platform.machine())

        def exclude(filename):
            if os.path.basename(filename) == 'SOURCES.txt':
                if os.path.dirname(filename).endswith('.egg-info'):
                    return True
            return False
        with tarfile.open(archive_path, 'w:bz2') as h:
            h.add(args.installspace, arcname=folder_name, exclude=exclude)
    elif args.os == 'windows':
        archive_path = 'ros2-package-windows-%s.zip' % platform.machine()
        with zipfile.ZipFile(archive_path, 'w') as zf:
            for dirname, subdirs, files in os.walk(args.installspace):
                arcname = os.path.join(
                    folder_name, os.path.relpath(dirname, start=args.installspace))
                zf.write(dirname, arcname=arcname)
                for filename in files:
                    if os.path.basename(filename) == 'SOURCES.txt':
                        if dirname.endswith('.egg-info'):
                            continue
                    filearcname = os.path.join(
                        folder_name, os.path.relpath(dirname, start=args.installspace), filename)
                    zf.write(os.path.join(dirname, filename), arcname=filearcname)
    else:
        raise RuntimeError('Unsupported operating system: %s' % args.os)
    info("created archive: '{0}'".format(archive_path))
    print('# END SUBSECTION')

    return 0


if __name__ == '__main__':
    sys.exit(main())

#------------------------------------------------------------------------------
# Copyright (c) 2010, Kurt W. Smith, Dag Sverre Seljebotn
# All rights reserved. See LICENSE.txt.
#------------------------------------------------------------------------------

# encoding: utf-8

import os, sys
import argparse
import logging
import textwrap
import glob
import tempfile
import shutil
from fwrap import fwrapper
from fwrap import configuration
from fwrap import git
from fwrap.configuration import Configuration

PROJECT_FILE = 'fwrap.json'

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()

def create_cmd(opts):
    if os.path.exists(opts.wrapper_pyx) and not opts.force:
        raise ValueError('File exists: %s' % opts.wrapper_pyx)
    cfg = Configuration(cmdline_options=opts)
    cfg.update_version()
    cfg.set_versioned_mode(opts.versioned)
    # Ensure that tree is clean, as we want to auto-commit
    if opts.versioned and not git.clean_index_and_workdir():
        raise RuntimeError('VCS state not clean, aborting')
    # Add wrapped files to configurtion
    for filename in opts.fortranfiles:
        cfg.add_wrapped_file(filename)
    # Create wrapper files
    created_files, routine_names = fwrapper.wrap(opts.fortranfiles, opts.wrapper_name,
                                                 cfg)
    # Commit
    if opts.versioned:
        if opts.message is None:
            opts.message = 'FWRAP Created wrapper %s' % opts.wrapper_pyx
        git.add(created_files)
        git.commit('%s\n\nFiles wrapped:\n%s' %
                   (opts.message, '\n'.join(opts.fortranfiles)))
        # That's it for content. However, we need to update the head
        # pointer to point to the commit just made. Simply search/replace
        # the file to make the change and commit again
        current_rev = git.cwd_rev()
        configuration.replace_in_file('head %s' % cfg.git_head(),
                                      'head %s' % current_rev,
                                      opts.wrapper_pyx,
                                      expected_count=1)
        git.add([opts.wrapper_pyx])
        git.commit('FWRAP Head record update in %s' % opts.wrapper_pyx)
        
    return 0

def print_file_status(filename):
    file_cfg = Configuration.create_from_file(filename)
    if file_cfg.version in (None, ''):
        return # not an Fwrapped file
    
    def status_label(has_changed):
        return 
    status_report = file_cfg.wrapped_files_status()
    any_changed = any(needs_update for f, needs_update in status_report)
    print '%s (%s):' % (filename,
                        'needs update, please run "fwrap update %s"' % filename
                        if any_changed else 'up to date')
    for wrapped_file, needs_update in status_report:
        print '    %s%s' % (wrapped_file,
                            ' (changed)' if needs_update else '')
    return any_changed

def status_cmd(opts):
    if len(opts.paths) == 0:
        if opts.recursive:
            opts.paths = ['.']
        else:
            opts.paths = glob.glob('*.pyx')
    for path in opts.paths:
        if not os.path.exists(path):
            raise ValueError('No such file or directory: %s' % path)
        if not opts.recursive and not os.path.isfile(path):
            raise ValueError('Please specify --recursive to query a directory')
    needs_update = False
    if opts.recursive:
        for path in opts.paths:
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    if filename.endswith('.pyx'):
                        needs_update = needs_update or print_file_status(filename)
    else:
        for filename in opts.paths:
            if os.path.isfile(filename) and filename.endswith('.pyx'):
                needs_update = needs_update or print_file_status(filename)
    return 1 if needs_update else 0


def mergepyf_cmd(opts):
    for f in [opts.wrapper_pyx, opts.pyf]:
        if not os.path.exists(f):
            raise ValueError('No such file: %s' % f)
    orig_cfg = Configuration.create_from_file(opts.wrapper_pyx)
    cfg = orig_cfg.copy()
    cfg.update_version()
    versioned = (cfg.vcs != 'none')
    if not versioned:
        raise NotImplementedError()
    
    # We want to compile a list of the routines not present in the pyf
    # (assumed to be explicitly excluded by the user). In order to do
    # this, first parse wrapped Fortran files to find routines present
    # in total.
    routines_in_fortran= set(fwrapper.find_routine_names(
        cfg.get_source_files(), cfg))
    
    # Now find names present in pyf file.
    # TODO: Fix, this ends up parsing twice
    routines_in_pyf = set(fwrapper.find_routine_names([opts.pyf], cfg))
    excluded_by_pyf = routines_in_fortran - routines_in_pyf
    cfg.exclude.extend([(routine, {})
                        for routine in excluded_by_pyf])

    if len(excluded_by_pyf) > 0:
        # Start with removing routines not present in pyf to keep
        # our history much cleaner
        orig_msg = opts.message
        opts.message = 'FWRAP Removing functions not present in %s' % opts.pyf
        update_cmd(opts, cfg, skip_head_commit=True)
        opts.message = orig_msg
    

def update_cmd(opts, cfg=None, skip_head_commit=False,
               branch_prefix='_fwrap'):
    if cfg is None:
        cfg = Configuration.create_from_file(opts.wrapper_pyx)
    any_changed = any(needs_update
                      for f, needs_update in cfg.wrapped_files_status())
    if not any_changed:
        print 'Already up to date'
        return 0
    else:
        print 'Needs update'

    if not git.is_tracked(opts.wrapper_pyx):
        raise RuntimeError('Not tracked by VCS, aborting: %s' % opts.wrapper_pyx)
    if not git.clean_index_and_workdir():
        raise RuntimeError('VCS state not clean, aborting')

    # First, generate wrappers (since Fortran files have changed
    # on *this* tree). But generate them into a temporary location.
    tmp_dir = tempfile.mkdtemp(prefix='fwrap-')
    try:
        fwrapper.wrap(cfg.get_source_files(),
                      opts.wrapper_name,
                      cfg,
                      output_directory=tmp_dir)
        temp_branch = git.create_temporary_branch(cfg.git_head(), branch_prefix)
        git.checkout(temp_branch)
    finally:
        shutil.rmtree(tmp_dir)
    
    
    
def no_project_response(opts):
    print textwrap.fill('Please run "fwrap init"; can not find project '
                        'file %s in this directory or any parent directory.' %
                        PROJECT_FILE)
    return 1

def create_argument_parser():
    parser = argparse.ArgumentParser(prog='fwrap',
                                     description='fwrap command line tool')
    subparsers = parser.add_subparsers(title='commands')

    #
    # create command
    #
    create = subparsers.add_parser('create')
    create.set_defaults(func=create_cmd)
    create.add_argument('-f', '--force', action='store_true',
                        help=('overwrite existing wrapper'))    
    create.add_argument('--versioned', action='store_true',
                        help=('allow modification of generated wrappers, and employ '
                              'VCS to manage changes (only git supported currently)'))    
    create.add_argument('-m', '--message',
                        help=('commit log message'))
    configuration.add_cmdline_options(create.add_argument)
    create.add_argument('wrapper_pyx')
    create.add_argument('fortranfiles', metavar='fortranfile', nargs='+')
    
    #
    # update command
    #
    update = subparsers.add_parser('update')
    update.set_defaults(func=update_cmd)
    update.add_argument('wrapper_pyx')

    #
    # mergepyf command
    #
    mergepyf = subparsers.add_parser('mergepyf')
    mergepyf.set_defaults(func=mergepyf_cmd)
    mergepyf.add_argument('wrapper_pyx')
    mergepyf.add_argument('pyf')

    #
    # status command
    #

    status = subparsers.add_parser('status')
    status.set_defaults(func=status_cmd)
    status.add_argument('-r', '--recursive', action='store_true',
                        help='Recurse subdirectories')
    status.add_argument('paths', metavar='path', nargs='*')

    return parser
    
def fwrap_main(args):
    argparser = create_argument_parser()
    opts = argparser.parse_args(args)
    if hasattr(opts, 'wrapper_pyx'):
        if not opts.wrapper_pyx.endswith('.pyx'):
            raise ValueError('Cython wrapper file name must end in .pyx')
        opts.wrapper_name = opts.wrapper_pyx[:-4]
    return opts.func(opts)

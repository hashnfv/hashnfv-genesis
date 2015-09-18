###############################################################################
# Copyright (c) 2015 Ericsson AB and others.
# szilard.cserey@ericsson.com
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache License, Version 2.0
# which accompanies this distribution, and is available at
# http://www.apache.org/licenses/LICENSE-2.0
###############################################################################


import os
import io
import re
import sys
import netaddr
import yaml

from dea import DeploymentEnvironmentAdapter
from dha import DeploymentHardwareAdapter
from install_fuel_master import InstallFuelMaster
from deploy_env import CloudDeploy
from execution_environment import ExecutionEnvironment
import common

log = common.log
exec_cmd = common.exec_cmd
err = common.err
warn = common.warn
check_file_exists = common.check_file_exists
check_dir_exists = common.check_dir_exists
create_dir_if_not_exists = common.create_dir_if_not_exists
delete = common.delete
check_if_root = common.check_if_root
ArgParser = common.ArgParser

FUEL_VM = 'fuel'
PATCH_DIR = 'fuel_patch'
WORK_DIR = '~/deploy'
CWD = os.getcwd()


class cd:

    def __init__(self, new_path):
        self.new_path = os.path.expanduser(new_path)

    def __enter__(self):
        self.saved_path = CWD
        os.chdir(self.new_path)

    def __exit__(self, etype, value, traceback):
        os.chdir(self.saved_path)


class AutoDeploy(object):

    def __init__(self, no_fuel, fuel_only, no_health_check, cleanup_only,
                 cleanup, storage_dir, pxe_bridge, iso_file, dea_file,
                 dha_file, fuel_plugins_dir):
        self.no_fuel = no_fuel
        self.fuel_only = fuel_only
        self.no_health_check = no_health_check
        self.cleanup_only = cleanup_only
        self.cleanup = cleanup
        self.storage_dir = storage_dir
        self.pxe_bridge = pxe_bridge
        self.iso_file = iso_file
        self.dea_file = dea_file
        self.dha_file = dha_file
        self.fuel_plugins_dir = fuel_plugins_dir
        self.dea = (DeploymentEnvironmentAdapter(dea_file)
                    if not cleanup_only else None)
        self.dha = DeploymentHardwareAdapter(dha_file)
        self.fuel_conf = {}
        self.fuel_node_id = self.dha.get_fuel_node_id()
        self.fuel_username, self.fuel_password = self.dha.get_fuel_access()
        self.tmp_dir = None

    def modify_ip(self, ip_addr, index, val):
        ip_str = str(netaddr.IPAddress(ip_addr))
        decimal_list = map(int, ip_str.split('.'))
        decimal_list[index] = val
        return '.'.join(map(str, decimal_list))

    def collect_fuel_info(self):
        self.fuel_conf['ip'] = self.dea.get_fuel_ip()
        self.fuel_conf['gw'] = self.dea.get_fuel_gateway()
        self.fuel_conf['dns1'] = self.dea.get_fuel_dns()
        self.fuel_conf['netmask'] = self.dea.get_fuel_netmask()
        self.fuel_conf['hostname'] = self.dea.get_fuel_hostname()
        self.fuel_conf['showmenu'] = 'yes'

    def install_fuel_master(self):
        log('Install Fuel Master')
        new_iso = '%s/deploy-%s' \
                  % (self.tmp_dir, os.path.basename(self.iso_file))
        self.patch_iso(new_iso)
        self.iso_file = new_iso
        self.install_iso()

    def install_iso(self):
        fuel = InstallFuelMaster(self.dea_file, self.dha_file,
                                 self.fuel_conf['ip'], self.fuel_username,
                                 self.fuel_password, self.fuel_node_id,
                                 self.iso_file, WORK_DIR,
                                 self.fuel_plugins_dir)
        fuel.install()

    def patch_iso(self, new_iso):
        tmp_orig_dir = '%s/origiso' % self.tmp_dir
        tmp_new_dir = '%s/newiso' % self.tmp_dir
        self.copy(tmp_orig_dir, tmp_new_dir)
        self.patch(tmp_new_dir, new_iso)

    def copy(self, tmp_orig_dir, tmp_new_dir):
        log('Copying...')
        os.makedirs(tmp_orig_dir)
        os.makedirs(tmp_new_dir)
        exec_cmd('fuseiso %s %s' % (self.iso_file, tmp_orig_dir))
        with cd(tmp_orig_dir):
            exec_cmd('find . | cpio -pd %s' % tmp_new_dir)
        with cd(tmp_new_dir):
            exec_cmd('fusermount -u %s' % tmp_orig_dir)
        delete(tmp_orig_dir)
        exec_cmd('chmod -R 755 %s' % tmp_new_dir)

    def patch(self, tmp_new_dir, new_iso):
        log('Patching...')
        patch_dir = '%s/%s' % (CWD, PATCH_DIR)
        ks_path = '%s/ks.cfg.patch' % patch_dir

        with cd(tmp_new_dir):
            exec_cmd('cat %s | patch -p0' % ks_path)
            delete('.rr_moved')
            isolinux = 'isolinux/isolinux.cfg'
            log('isolinux.cfg before: %s'
                % exec_cmd('grep netmask %s' % isolinux))
            self.update_fuel_isolinux(isolinux)
            log('isolinux.cfg after: %s'
                % exec_cmd('grep netmask %s' % isolinux))

            iso_linux_bin = 'isolinux/isolinux.bin'
            exec_cmd('mkisofs -quiet -r -J -R -b %s '
                     '-no-emul-boot -boot-load-size 4 '
                     '-boot-info-table -hide-rr-moved '
                     '-x "lost+found:" -o %s .'
                     % (iso_linux_bin, new_iso))

    def update_fuel_isolinux(self, file):
        with io.open(file) as f:
            data = f.read()
        for key, val in self.fuel_conf.iteritems():
            pattern = r'%s=[^ ]\S+' % key
            replace = '%s=%s' % (key, val)
            data = re.sub(pattern, replace, data)
        with io.open(file, 'w') as f:
            f.write(data)

    def deploy_env(self):
        dep = CloudDeploy(self.dea, self.dha, self.fuel_conf['ip'],
                          self.fuel_username, self.fuel_password,
                          self.dea_file, WORK_DIR, self.no_health_check)
        return dep.deploy()

    def setup_execution_environment(self):
        exec_env = ExecutionEnvironment(self.storage_dir, self.pxe_bridge,
                                        self.dha_file, self.dea)
        exec_env.setup_environment()

    def cleanup_execution_environment(self):
        exec_env = ExecutionEnvironment(self.storage_dir, self.pxe_bridge,
                                        self.dha_file, self.dea)
        exec_env.cleanup_environment()

    def create_tmp_dir(self):
        self.tmp_dir = '%s/fueltmp' % CWD
        delete(self.tmp_dir)
        create_dir_if_not_exists(self.tmp_dir)

    def deploy(self):
        self.collect_fuel_info()
        if not self.no_fuel:
            self.setup_execution_environment()
            self.create_tmp_dir()
            self.install_fuel_master()
        if not self.fuel_only:
            return self.deploy_env()
        return True

    def run(self):
        check_if_root()
        if self.cleanup_only:
            self.cleanup_execution_environment()
        else:
            deploy_success = self.deploy()
            if self.cleanup:
                self.cleanup_execution_environment()
            return deploy_success
        return True

def check_bridge(pxe_bridge, dha_path):
    with io.open(dha_path) as yaml_file:
        dha_struct = yaml.load(yaml_file)
    if dha_struct['adapter'] != 'libvirt':
        log('Using Linux Bridge %s for booting up the Fuel Master VM'
            % pxe_bridge)
        r = exec_cmd('ip link show %s' % pxe_bridge)
        if pxe_bridge in r and 'state DOWN' in r:
            err('Linux Bridge {0} is not Active, bring'
                ' it UP first: [ip link set dev {0} up]'.format(pxe_bridge))


def check_fuel_plugins_dir(dir):
    msg = None
    if not dir:
        msg = 'Fuel Plugins Directory not specified!'
    elif not os.path.isdir(dir):
        msg = 'Fuel Plugins Directory does not exist!'
    elif not os.listdir(dir):
        msg = 'Fuel Plugins Directory is empty!'
    if msg:
        warn('%s Opendaylight Fuel Plugin will not be installed!' % msg)


def parse_arguments():
    parser = ArgParser(prog='python %s' % __file__)
    parser.add_argument('-nf', dest='no_fuel', action='store_true',
                        default=False,
                        help='Do not install Fuel Master (and Node VMs when '
                             'using libvirt)')
    parser.add_argument('-nh', dest='no_health_check', action='store_true',
                        default=False,
                        help='Don\'t run health check after deployment')
    parser.add_argument('-fo', dest='fuel_only', action='store_true',
                        default=False,
                        help='Install Fuel Master only (and Node VMs when '
                             'using libvirt)')
    parser.add_argument('-co', dest='cleanup_only', action='store_true',
                        default=False,
                        help='Cleanup VMs and Virtual Networks according to '
                             'what is defined in DHA')
    parser.add_argument('-c', dest='cleanup', action='store_true',
                        default=False,
                        help='Cleanup after deploy')
    if {'-iso', '-dea', '-dha', '-h'}.intersection(sys.argv):
        parser.add_argument('-iso', dest='iso_file', action='store', nargs='?',
                            default='%s/OPNFV.iso' % CWD,
                            help='ISO File [default: OPNFV.iso]')
        parser.add_argument('-dea', dest='dea_file', action='store', nargs='?',
                            default='%s/dea.yaml' % CWD,
                            help='Deployment Environment Adapter: dea.yaml')
        parser.add_argument('-dha', dest='dha_file', action='store', nargs='?',
                            default='%s/dha.yaml' % CWD,
                            help='Deployment Hardware Adapter: dha.yaml')
    else:
        parser.add_argument('iso_file', action='store', nargs='?',
                            default='%s/OPNFV.iso' % CWD,
                            help='ISO File [default: OPNFV.iso]')
        parser.add_argument('dea_file', action='store', nargs='?',
                            default='%s/dea.yaml' % CWD,
                            help='Deployment Environment Adapter: dea.yaml')
        parser.add_argument('dha_file', action='store', nargs='?',
                            default='%s/dha.yaml' % CWD,
                            help='Deployment Hardware Adapter: dha.yaml')
    parser.add_argument('-s', dest='storage_dir', action='store',
                        default='%s/images' % CWD,
                        help='Storage Directory [default: images]')
    parser.add_argument('-b', dest='pxe_bridge', action='store',
                        default='pxebr',
                        help='Linux Bridge for booting up the Fuel Master VM '
                             '[default: pxebr]')
    parser.add_argument('-p', dest='fuel_plugins_dir', action='store',
                        help='Fuel Plugins directory')

    args = parser.parse_args()
    log(args)

    check_file_exists(args.dha_file)

    if not args.cleanup_only:
        check_file_exists(args.dea_file)
        check_fuel_plugins_dir(args.fuel_plugins_dir)

    if not args.no_fuel and not args.cleanup_only:
        log('Using OPNFV ISO file: %s' % args.iso_file)
        check_file_exists(args.iso_file)
        log('Using image directory: %s' % args.storage_dir)
        create_dir_if_not_exists(args.storage_dir)
        check_bridge(args.pxe_bridge, args.dha_file)

    kwargs = {'no_fuel': args.no_fuel, 'fuel_only': args.fuel_only,
              'no_health_check': args.no_health_check,
              'cleanup_only': args.cleanup_only, 'cleanup': args.cleanup,
              'storage_dir': args.storage_dir, 'pxe_bridge': args.pxe_bridge,
              'iso_file': args.iso_file, 'dea_file': args.dea_file,
              'dha_file': args.dha_file,
              'fuel_plugins_dir': args.fuel_plugins_dir}
    return kwargs


def main():
    kwargs = parse_arguments()

    d = AutoDeploy(**kwargs)
    sys.exit(d.run())

if __name__ == '__main__':
    main()

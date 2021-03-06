# Copyright (C) 2017 Canonical Ltd.
#
# Author: Chad Smith <chad.smith@canonical.com>
#
# This file is part of cloud-init. See LICENSE file for license information.

import logging
import os
import re

from cloudinit.net import find_fallback_nic, get_devicelist
from cloudinit import util

LOG = logging.getLogger(__name__)


class InvalidDHCPLeaseFileError(Exception):
    """Raised when parsing an empty or invalid dhcp.leases file.

    Current uses are DataSourceAzure and DataSourceEc2 during ephemeral
    boot to scrape metadata.
    """
    pass


def maybe_perform_dhcp_discovery(nic=None):
    """Perform dhcp discovery if nic valid and dhclient command exists.

    If the nic is invalid or undiscoverable or dhclient command is not found,
    skip dhcp_discovery and return an empty dict.

    @param nic: Name of the network interface we want to run dhclient on.
    @return: A dict of dhcp options from the dhclient discovery if run,
        otherwise an empty dict is returned.
    """
    if nic is None:
        nic = find_fallback_nic()
        if nic is None:
            LOG.debug(
                'Skip dhcp_discovery: Unable to find fallback nic.')
            return {}
    elif nic not in get_devicelist():
        LOG.debug(
            'Skip dhcp_discovery: nic %s not found in get_devicelist.', nic)
        return {}
    dhclient_path = util.which('dhclient')
    if not dhclient_path:
        LOG.debug('Skip dhclient configuration: No dhclient command found.')
        return {}
    with util.tempdir(prefix='cloud-init-dhcp-') as tmpdir:
        return dhcp_discovery(dhclient_path, nic, tmpdir)


def parse_dhcp_lease_file(lease_file):
    """Parse the given dhcp lease file for the most recent lease.

    Return a dict of dhcp options as key value pairs for the most recent lease
    block.

    @raises: InvalidDHCPLeaseFileError on empty of unparseable leasefile
        content.
    """
    lease_regex = re.compile(r"lease {(?P<lease>[^}]*)}\n")
    dhcp_leases = []
    lease_content = util.load_file(lease_file)
    if len(lease_content) == 0:
        raise InvalidDHCPLeaseFileError(
            'Cannot parse empty dhcp lease file {0}'.format(lease_file))
    for lease in lease_regex.findall(lease_content):
        lease_options = []
        for line in lease.split(';'):
            # Strip newlines, double-quotes and option prefix
            line = line.strip().replace('"', '').replace('option ', '')
            if not line:
                continue
            lease_options.append(line.split(' ', 1))
        dhcp_leases.append(dict(lease_options))
    if not dhcp_leases:
        raise InvalidDHCPLeaseFileError(
            'Cannot parse dhcp lease file {0}. No leases found'.format(
                lease_file))
    return dhcp_leases


def dhcp_discovery(dhclient_cmd_path, interface, cleandir):
    """Run dhclient on the interface without scripts or filesystem artifacts.

    @param dhclient_cmd_path: Full path to the dhclient used.
    @param interface: Name of the network inteface on which to dhclient.
    @param cleandir: The directory from which to run dhclient as well as store
        dhcp leases.

    @return: A dict of dhcp options parsed from the dhcp.leases file or empty
        dict.
    """
    LOG.debug('Performing a dhcp discovery on %s', interface)

    # XXX We copy dhclient out of /sbin/dhclient to avoid dealing with strict
    # app armor profiles which disallow running dhclient -sf <our-script-file>.
    # We want to avoid running /sbin/dhclient-script because of side-effects in
    # /etc/resolv.conf any any other vendor specific scripts in
    # /etc/dhcp/dhclient*hooks.d.
    sandbox_dhclient_cmd = os.path.join(cleandir, 'dhclient')
    util.copy(dhclient_cmd_path, sandbox_dhclient_cmd)
    pid_file = os.path.join(cleandir, 'dhclient.pid')
    lease_file = os.path.join(cleandir, 'dhcp.leases')

    # ISC dhclient needs the interface up to send initial discovery packets.
    # Generally dhclient relies on dhclient-script PREINIT action to bring the
    # link up before attempting discovery. Since we are using -sf /bin/true,
    # we need to do that "link up" ourselves first.
    util.subp(['ip', 'link', 'set', 'dev', interface, 'up'], capture=True)
    cmd = [sandbox_dhclient_cmd, '-1', '-v', '-lf', lease_file,
           '-pf', pid_file, interface, '-sf', '/bin/true']
    util.subp(cmd, capture=True)
    return parse_dhcp_lease_file(lease_file)


# vi: ts=4 expandtab

#!/usr/bin/python
# Copyright 2011 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# unittest requires method names starting in 'test'
# pylint:disable=invalid-name

"""Unit tests for netdev.py implementation."""

__author__ = 'dgentry@google.com (Denton Gentry)'

import google3
from tr.wvtest import unittest
import tr.session
import netdev


class NetdevTest(unittest.TestCase):
  """Tests for netdev.py."""

  def setUp(self):
    self._old_PROC_NET_DEV = netdev.PROC_NET_DEV

  def tearDown(self):
    netdev.PROC_NET_DEV = self._old_PROC_NET_DEV

  def testInterfaceStatsGood(self):
    netdev.PROC_NET_DEV = 'testdata/ethernet/net_dev'
    eth = netdev.NetdevStatsLinux26(ifname='foo0')
    self.assertEqual(eth.BroadcastPacketsReceived, 0)
    self.assertEqual(eth.BroadcastPacketsSent, 0)
    self.assertEqual(eth.BytesReceived, 1)
    self.assertEqual(eth.BytesSent, 9)
    self.assertEqual(eth.DiscardPacketsReceived, 9)
    self.assertEqual(eth.DiscardPacketsSent, 12)
    self.assertEqual(eth.ErrorsReceived, 9)
    self.assertEqual(eth.ErrorsSent, 11 + 13)
    self.assertEqual(eth.MulticastPacketsReceived, 8)
    self.assertEqual(eth.MulticastPacketsSent, 0)
    self.assertEqual(eth.PacketsReceived, 100)
    self.assertEqual(eth.PacketsSent, 10)
    self.assertEqual(eth.UnicastPacketsReceived, 92)
    self.assertEqual(eth.UnicastPacketsSent, 10)
    self.assertEqual(eth.UnknownProtoPacketsReceived, 0)

  def testInterfaceStatsReal(self):
    # A test using a /proc/net/dev line taken from a running Linux 2.6.32
    # system. Most of the fields are zero, so we exercise the other handling
    # using the foo0 fake data instead.
    netdev.PROC_NET_DEV = 'testdata/ethernet/net_dev'
    eth = netdev.NetdevStatsLinux26('eth0')
    self.assertEqual(eth.BroadcastPacketsReceived, 0)
    self.assertEqual(eth.BroadcastPacketsSent, 0)
    self.assertEqual(eth.BytesReceived, 21052761139)
    self.assertEqual(eth.BytesSent, 10372833035)
    self.assertEqual(eth.DiscardPacketsReceived, 0)
    self.assertEqual(eth.DiscardPacketsSent, 0)
    self.assertEqual(eth.ErrorsReceived, 0)
    self.assertEqual(eth.ErrorsSent, 0)
    self.assertEqual(eth.MulticastPacketsReceived, 0)
    self.assertEqual(eth.MulticastPacketsSent, 0)
    self.assertEqual(eth.PacketsReceived, 91456760)
    self.assertEqual(eth.PacketsSent, 80960002)
    self.assertEqual(eth.UnicastPacketsReceived, 91456760)
    self.assertEqual(eth.UnicastPacketsSent, 80960002)
    self.assertEqual(eth.UnknownProtoPacketsReceived, 0)

  def testSysfsStats(self):
    qfiles = 'testdata/sysfs/eth0/bcmgenet_discard_cnt_q%d'
    numq = 17
    eth = netdev.NetdevStatsLinux26('eth0', qfiles=qfiles,
                                    numq=numq, hipriq=numq)
    self.assertEqual(len(eth.X_CATAWAMPUS_ORG_DiscardFrameCnts), numq)
    total = 0
    for i in range(numq):
      self.assertEqual(int(eth.X_CATAWAMPUS_ORG_DiscardFrameCnts[i]), i)
      total += i
    self.assertEqual(eth.X_CATAWAMPUS_ORG_DiscardPacketsReceivedHipri, total)

    numq = 5
    eth = netdev.NetdevStatsLinux26('eth0', qfiles=qfiles, numq=numq, hipriq=2)
    self.assertEqual(len(eth.X_CATAWAMPUS_ORG_DiscardFrameCnts), numq)
    for i in range(numq):
      self.assertEqual(int(eth.X_CATAWAMPUS_ORG_DiscardFrameCnts[i]), i)
    self.assertEqual(eth.X_CATAWAMPUS_ORG_DiscardPacketsReceivedHipri, 1)

    eth = netdev.NetdevStatsLinux26('foo0', qfiles=qfiles, numq=0)
    self.assertEqual(len(eth.X_CATAWAMPUS_ORG_DiscardFrameCnts), 0)
    self.assertEqual(eth.X_CATAWAMPUS_ORG_DiscardPacketsReceivedHipri, 0)

  def testRxPacketsWrap(self):
    """Rx Packets has wrapped back to zero, but Rx Multicast has not."""
    netdev.PROC_NET_DEV = 'testdata/netdev/wrapped_net_dev'
    eth = netdev.NetdevStatsLinux26('eth0')
    self.assertEqual(eth.MulticastPacketsReceived, 10)
    self.assertEqual(eth.PacketsReceived, 1)
    # b/12022359 would try to set UnicastPacketsReceived negative, and result
    # in a ValueError. We want to check that no exception is raised.
    self.assertGreaterEqual(eth.UnicastPacketsReceived, 0)

  def test32bitCounterWraps(self):
    netdev.PROC_NET_DEV = 'testdata/netdev/proc_net_dev_wrap1'
    eth = netdev.NetdevStatsLinux26(ifname='eth0')
    self.assertEqual(eth.BroadcastPacketsReceived, 0)
    self.assertEqual(eth.BroadcastPacketsSent, 0)
    self.assertEqual(eth.BytesReceived, 4294967231)
    self.assertEqual(eth.BytesSent, 4294967239)
    self.assertEqual(eth.DiscardPacketsReceived, 4294967234 + 4294967235)
    self.assertEqual(eth.DiscardPacketsSent, 4294967242)
    self.assertEqual(eth.ErrorsReceived, 4294967233 + 4294967236)
    self.assertEqual(eth.ErrorsSent, 4294967241 + 4294967243)
    self.assertEqual(eth.MulticastPacketsReceived, 4294967200)
    self.assertEqual(eth.MulticastPacketsSent, 0)
    self.assertEqual(eth.PacketsReceived, 4294967232)
    self.assertEqual(eth.PacketsSent, 4294967240)
    self.assertEqual(eth.UnicastPacketsReceived, 4294967232 - 4294967200)
    self.assertEqual(eth.UnicastPacketsSent, 4294967240)
    self.assertEqual(eth.UnknownProtoPacketsReceived, 0)
    netdev.PROC_NET_DEV = 'testdata/netdev/proc_net_dev_wrap2'
    # vaues should be cached for one CWMP session
    self.assertEqual(eth.BytesReceived, 4294967231)
    tr.session.cache.flush()
    # Now we should see the accumulated values
    MAX_UINT = 0xffffffff
    self.assertEqual(eth.BytesReceived, MAX_UINT + 1000)
    self.assertEqual(eth.BytesSent, MAX_UINT + 1008)
    self.assertEqual(eth.DiscardPacketsReceived, 2*MAX_UINT + 1003 + 1004)
    self.assertEqual(eth.DiscardPacketsSent, MAX_UINT + 1011)
    self.assertEqual(eth.ErrorsReceived, 2*MAX_UINT + 1002 + 1005)
    self.assertEqual(eth.ErrorsSent, 2*MAX_UINT + 1010 + 1012)
    self.assertEqual(eth.MulticastPacketsReceived, MAX_UINT + 1007)
    self.assertEqual(eth.PacketsReceived, MAX_UINT + 1021)
    self.assertEqual(eth.PacketsSent, MAX_UINT + 1009)
    self.assertEqual(eth.UnicastPacketsReceived, 1021 - 1007)
    self.assertEqual(eth.UnicastPacketsSent, MAX_UINT + 1009)


if __name__ == '__main__':
  unittest.main()

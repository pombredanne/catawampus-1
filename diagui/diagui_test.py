"""Unit Tests for diagui.py implementation."""

__author__ = 'anandkhare@google.com (Anand Khare)'

import ast
import json
import os
import google3
import diagui.main
import tornado.httpclient
import tr.mainloop
import tr.helpers
import dm_root
import dm.fakewifi
import dm.host
from tr.wvtest import unittest


class AsynchFetch(object):
  """Creates instance of client object, makes asynchronous calls to server."""

  def __init__(self, url_temp):
    self.http_client = tornado.httpclient.AsyncHTTPClient()
    self.resp = None
    self.http_client.fetch(url_temp, method='GET',
                           callback=self.HandleRequest)

  def HandleRequest(self, response):
    self.resp = response

  def Wait(self, loop):
    while not self.resp:
      loop.RunOnce()


class FakeHostsList(dm.host.CATA181HOSTS):

  def __init__(self, count=1):
    self._hosts = {}
    for idx in range(1, count+1):
      host = tr.core.Extensible(dm.host.CATA181HOST)()
      host.X_CATAWAMPUS_ORG_ClientIdentification = (
          dm.host.ClientIdentification())
      self._hosts[str(idx)] = host

  @property
  def HostList(self):
    return self._hosts


class DiaguiTest(unittest.TestCase):
  """Tests whether 2 clients receive the same data from the server.

     Also checks if both receive updates.
  """

  def setUp(self):
    self.save_activewan = diagui.main.ACTIVEWAN
    diagui.main.ACTIVEWAN = 'testdata/activewan'
    self.checksum = '0'
    self.url_string = 'http://localhost:8880/content.json?checksum='

  def tearDown(self):
    diagui.main.ACTIVEWAN = self.save_activewan

  def testUpdateDict(self):
    test_data = """acs OK (May 21 2013 18:58:41+700)
softversion 1.16a
uptime 76:28:39
serialnumber 123456789
temperature 54 C
fiberjack Up
wanmac 1a:2b:3c:4d:5e:6f
wanip 63.28.214.97
lanip 192.168.1.1
subnetmask 255.255.255.0
dhcpstart 192.158.1.100
dhcpend 192.168.1.254
wiredlan 6a:5b:4c:3d:2e:1f Up
wireddevices Living Room (TV box, 6a:5b:4c:3d:2e:1f)
ssid24 AllenFamilyNetwork
ssid5 (same)
wpa2 (configured)
wirelesslan 3a:1b:4c:1d:5e:9f Up
wirelessdevices Dad\'s Phone (6a:5b:4c:3d:2e:1f)
upnp O
portforwarding 80-80: Dad\'s Computer (6a:5b:4c:3d:2e:1f)
dmzdevice Wireless Device (1) (6a:5b:4c:3d:2e:1f)
dyndns DynDNS
username allenfamily
domain home.allenfamily.com"""

    url_temp = self.url_string + self.checksum
    app = diagui.main.MainApplication(None, None, run_diagui=True)
    app.listen(8880)
    app.diagui.data = dict(line.decode('utf-8').strip().split(None, 1)
                           for line in test_data.split('\n'))
    app.diagui.UpdateCheckSum()
    response1 = AsynchFetch(url_temp)
    response2 = AsynchFetch(url_temp)
    main_loop = tr.mainloop.MainLoop()
    response1.Wait(main_loop)
    response2.Wait(main_loop)
    self.assertEqual(response1.resp.body,
                     response2.resp.body)
    self.assertNotEqual(response1.resp.body, None)
    self.checksum = ast.literal_eval(response1.resp.body).get(
        'checksum')
    test_data = """acs OK (May 21 2013 18:58:41+700)
softversion 2.16a
uptime 76:28:39
serialnumber 987654321
temperature 54 C
fiberjack Up
wanmac 1a:2b:3c:4d:5e:6f
wanip 63.28.214.97
lanip 192.168.1.1
subnetmask 255.255.255.0
dhcpstart 192.158.1.100
dhcpend 192.168.1.254
wiredlan 6a:5b:4c:3d:2e:1f Up
wireddevices Living Room (TV box, 6a:5b:4c:3d:2e:1f)
ssid24 AllenFamilyNetwork
ssid5 (same)
wpa2 (configured)
wirelesslan 3a:1b:4c:1d:5e:9f Up
wirelessdevices Dad\'s Phone (6a:5b:4c:3d:2e:1f)
upnp O
portforwarding 80-80: Dad\'s Computer (6a:5b:4c:3d:2e:1f)
dmzdevice Wireless Device (1) (6a:5b:4c:3d:2e:1f)
dyndns DynDNS
username allenfamily
domain home.allenfamily.com"""
    app.diagui.data = dict(line.decode('utf-8').strip().split(None, 1)
                           for line in test_data.split('\n'))
    app.diagui.UpdateCheckSum()
    url_temp = self.url_string + self.checksum
    response1_new = AsynchFetch(url_temp)
    response2_new = AsynchFetch(url_temp)
    response1_new.Wait(main_loop)
    response2_new.Wait(main_loop)
    self.assertEqual(response1_new.resp.body,
                     response2_new.resp.body)
    self.assertNotEqual(response1_new.resp.body, None)
    self.assertNotEqual(response1.resp.body,
                        response1_new.resp.body)

  def testOnuStats(self):
    app = diagui.main.MainApplication(None, None, run_diagui=True)
    app.listen(8880)
    main_loop = tr.mainloop.MainLoop()
    diagui.main.ONU_STAT_FILE = 'testdata/onu_stats1.json'
    app.diagui.UpdateOnuStats()
    self.assertTrue('onu_wan_connected' in app.diagui.data)
    self.assertFalse('onu_serial' in app.diagui.data)
    self.checksum = '0'
    url_temp = self.url_string + self.checksum
    response = AsynchFetch(url_temp)
    response.Wait(main_loop)
    self.assertNotEqual(response.resp.body, None)
    jsdata = json.loads(response.resp.body)
    self.assertTrue(jsdata['onu_wan_connected'])

    diagui.main.ONU_STAT_FILE = 'testdata/onu_stats2.json'
    app.diagui.UpdateOnuStats()
    response = AsynchFetch(url_temp)
    response.Wait(main_loop)
    jsdata = json.loads(response.resp.body)
    self.assertTrue(jsdata['onu_wan_connected'])
    self.assertTrue(jsdata['onu_acs_contacted'])
    self.assertEqual(jsdata['onu_acs_contact_time'], 100000)
    self.assertEqual(jsdata['onu_serial'], '12345')

  def testNoOnuStats(self):
    app = diagui.main.MainApplication(None, None, run_diagui=True)
    diagui.main.ONU_STAT_FILE = '/no/such/file'
    app.diagui.UpdateOnuStats()
    # just checking whether there is an exception


class TechuiTest(unittest.TestCase):
  """Tests the data gathering functions for the TechUI."""

  def testMainApp(self):
    url = 'http://localhost:8880/techui.json?checksum=0'
    app = diagui.main.MainApplication(None, None, run_diagui=True,
                                      run_techui=True)
    fake_data = {'moca_bitloading': {},
                 'ip_addr': {'ec:88:92:91:3d:67': '111.111.11.1',
                             'aa:aa:aa:aa:aa:aa': '123.456.78.90'},
                 'wifi_signal_strength': {},
                 'softversion': 'gfrg200-46-pre0-39-g056a912-th',
                 'serialnumber': 'G0123456789',
                 'other_aps': {'f4:f5:e8:80:58:d7': -67.0},
                 'host_names': {'ec:88:92:91:3d:67': 'android',
                                'aa:aa:aa:aa:aa:aa': 'GFiberTV'},
                 'moca_corrected_codewords': {},
                 'moca_uncorrected_codewords': {},
                 'moca_signal_strength': {},
                 'self_signals': {'f4:f5:e8:83:01:94': -25},
                 'moca_nbas': {},
                 'checksum': 0}
    app.techui.data = fake_data
    app.listen(8880)
    main_loop = tr.mainloop.MainLoop()
    response1 = AsynchFetch(url)
    response1.Wait(main_loop)
    result1 = json.loads(response1.resp.body)
    self.assertNotEqual(result1, None)
    self.assertEqual(result1, fake_data)

    # Send another request, update the data, and call callbacks.
    # Should update the checksum.
    result1_checksum = result1['checksum']
    response2 = AsynchFetch(url)
    app.techui.data['other_aps'] = {'f4:f5:e8:80:58:d7': -50.0}
    app.techui.NotifyUpdatedDict()
    response2.Wait(main_loop)
    result2 = json.loads(response2.resp.body)

    # Set fake data to expected output and compare.
    fake_data['other_aps'] = {'f4:f5:e8:80:58:d7': -50.0}
    fake_data['checksum'] = app.techui.data['checksum']
    result2_checksum = result2['checksum']
    self.assertNotEqual(result2, None)
    self.assertEqual(result2, fake_data)
    self.assertNotEqual(result1_checksum, result2_checksum)
    self.assertEqual(app.techui.FindTVBoxes(), ['123.456.78.90'])

    # Update the url to have the new checksum, update data, and check for
    # correct response.
    url = 'http://localhost:8880/techui.json?checksum=' + result2_checksum
    response3 = AsynchFetch(url)
    app.techui.data['other_aps'] = {'f4:f5:e8:80:58:d7': -40.0}
    app.techui.NotifyUpdatedDict()
    response3.Wait(main_loop)
    result3 = json.loads(response3.resp.body)

    # Set fake data to expected output and compare.
    fake_data['other_aps'] = {'f4:f5:e8:80:58:d7': -40.0}
    fake_data['checksum'] = app.techui.data['checksum']
    result3_checksum = result3['checksum']
    self.assertNotEqual(result3, None)
    self.assertEqual(result3, fake_data)
    self.assertNotEqual(result2_checksum, result3_checksum)

  def testSetTechUIDict(self):
    techui = diagui.main.TechUI(None)
    techui.SetTechUIDict('fake', {})
    self.assertEqual(techui.data['fake'], {})
    test_dict = {'11:22:33:44:55:66': 1, '11:22:33:44:55:67': 2}
    techui.SetTechUIDict('fake', test_dict)
    self.assertEqual(techui.data['fake'], test_dict)

  def testLoadJson(self):
    dne = '/tmp/does_not_exist'
    try:
      os.remove(dne)
    except OSError:
      pass
    result = diagui.main.LoadJson(dne)
    self.assertEqual(result, {})

    jsonfile = '/tmp/json'
    test_dict = {'11:22:33:44:55:66': 1, '11:22:33:44:55:67': 2}
    tr.helpers.WriteFileAtomic(jsonfile, json.dumps(test_dict))
    result = diagui.main.LoadJson(jsonfile)
    self.assertEqual(result, test_dict)
    try:
      os.remove(jsonfile)
    except OSError:
      pass

  def testUpdateMocaDict(self):
    techui = diagui.main.TechUI(None)
    techui.root = dm_root.DeviceModelRoot(None, 'fakecpe', None)
    interface_list = techui.root.Device.MoCA.InterfaceList
    snr = {}
    bitloading = {}
    corrected_cw = {}
    uncorrected_cw = {}
    nbas = {}
    for unused_i, inter in interface_list.iteritems():
      for unused_j, dev in inter.AssociatedDeviceList.iteritems():
        snr[dev.MACAddress] = dev.X_CATAWAMPUS_ORG_RxSNR_dB
        bitloading[dev.MACAddress] = dev.X_CATAWAMPUS_ORG_RxBitloading
        nbas[dev.MACAddress] = dev.X_CATAWAMPUS_ORG_RxNBAS
        corrected = (dev.X_CATAWAMPUS_ORG_RxPrimaryCwCorrected +
                     dev.X_CATAWAMPUS_ORG_RxSecondaryCwCorrected)
        uncorrected = (dev.X_CATAWAMPUS_ORG_RxPrimaryCwUncorrected +
                       dev.X_CATAWAMPUS_ORG_RxSecondaryCwUncorrected)
        no_errors = (dev.X_CATAWAMPUS_ORG_RxPrimaryCwNoErrors +
                     dev.X_CATAWAMPUS_ORG_RxSecondaryCwNoErrors)
        total = corrected + uncorrected + no_errors
        if total > 0:
          corrected_cw[dev.MACAddress] = corrected/total
          uncorrected_cw[dev.MACAddress] = uncorrected/total
        else:
          corrected_cw[dev.MACAddress] = 0
          uncorrected_cw[dev.MACAddress] = 0
    techui.UpdateMocaDict()
    self.assertEqual(snr, techui.data['moca_signal_strength'])
    self.assertEqual(bitloading, techui.data['moca_bitloading'])
    self.assertEqual(corrected_cw,
                     techui.data['moca_corrected_codewords'])
    self.assertEqual(uncorrected_cw,
                     techui.data['moca_uncorrected_codewords'])
    self.assertEqual(nbas, techui.data['moca_nbas'])

  def testUpdateWifiDict(self):
    techui = diagui.main.TechUI(None)
    wlan0 = dm.fakewifi.FakeWifiWlanConfiguration()
    wlan1 = dm.fakewifi.FakeWifiWlanConfiguration()
    techui.root = dm_root.DeviceModelRoot(None, 'fakecpe', None)
    lans = techui.root.InternetGatewayDevice.LANDeviceList
    lans['1'].WLANConfigurationList = {
        '1': wlan0,
        '2': wlan1,
    }
    wlan0.signals = {'11:22:33:44:55:66': -66}
    wlan1.signals = {'66:55:44:33:22:11': -11}

    techui.UpdateWifiDict()
    self.assertEquals(
        techui.data['wifi_signal_strength'],
        {'66:55:44:33:22:11': -11, '11:22:33:44:55:66': -66})

  def testNoSignals(self):
    techui = diagui.main.TechUI(None)
    wlan0 = dm.fakewifi.FakeWifiWlanConfiguration()
    wlan1 = object()
    techui.root = dm_root.DeviceModelRoot(None, 'fakecpe', None)
    lans = techui.root.InternetGatewayDevice.LANDeviceList
    lans['1'].WLANConfigurationList = {
        '1': wlan0,
        '2': wlan1,
    }
    wlan0.signals = {'11:22:33:44:55:66': -66}

    techui.UpdateWifiDict()
    self.assertEquals(
        techui.data['wifi_signal_strength'],
        {'11:22:33:44:55:66': -66})


class LicenseuiTest(unittest.TestCase):
  """Make sure server can retrieve encrypted license file."""

  def testLicenseExists(self):
    app = diagui.main.MainApplication(None, None, run_licenseui=True)
    app.listen(8880)
    main_loop = tr.mainloop.MainLoop()
    response = AsynchFetch('http://localhost:8880/license/LICENSES.zip')
    response.Wait(main_loop)
    self.assertNotEqual(response.resp.body, None)


if __name__ == '__main__':
  unittest.main()

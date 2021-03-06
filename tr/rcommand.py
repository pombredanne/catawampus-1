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

# TR-069 has mandatory attribute names that don't comply with policy
# pylint:disable=invalid-name
#
"""A simple command protocol that lets us manipulate a TR-069 tree."""

__author__ = 'apenwarr@google.com (Avery Pennarun)'


import traceback
import core
import download
import mainloop
import quotedblock
import session


class RemoteCommandStreamer(quotedblock.QuotedBlockStreamer):
  """A simple command protocol that lets us manipulate a TR-069 tree."""

  def __init__(self, sock, address, root, state_machine):
    """Initialize a RemoteCommandStreamer.

    Args:
      sock: the socket provided by mainloop.Listen
      address: the address provided by mainloop.Listen
      root: the root of the TR-069 (core.Exporter) object tree.
      state_machine:  The http.CPEStateMachine returned by http.Listen.
    """
    quotedblock.QuotedBlockStreamer.__init__(self, sock, address)
    self.root = root
    self.state_machine = state_machine
    self.download_manager = download.DownloadManager()

  def _ProcessBlock(self, lines):
    if not lines:
      raise Exception('try the "help" command')
    for words in lines:
      cmd, args = words[0], tuple(words[1:])
      funcname = 'Cmd%s' % cmd.title()
      print 'command: %r %r' % (cmd, args)
      func = getattr(self, funcname, None)
      if not func:
        raise Exception('no such command %r' % (cmd,))
      yield func(*args)

  def ProcessBlock(self, lines):
    """Process an incoming list of commands and return the result."""
    try:
      out = sum((list(i) for i in self._ProcessBlock(lines)), [])
    except EOFError:
      raise
    except Exception as e:  # pylint:disable=broad-except
      print traceback.format_exc()
      return [['ERROR', '-1', str(e)]]
    session.cache.flush()
    return [['OK']] + out

  def CmdHelp(self):
    """Return a list of available commands."""
    for name in sorted(dir(self)):
      if name.startswith('Cmd'):
        func = getattr(self, name)
        yield [name[3:].lower(), func.__doc__ or '']

  def CmdQuit(self):
    """Close the current connection."""
    raise EOFError()

  def CmdQuitquitquit(self):
    """Shut down the server entirely."""
    exit(123)

  def CmdCompletions(self, prefix):
    """Return possible completions for the given name prefix."""
    parts = prefix.split('.')
    before, after = parts[:-1], parts[-1]
    for name in self.root.ListExports('.'.join(before), recursive=False):
      if name.lower().startswith(after.lower()):
        print '  completion: %r %r' % (before, name)
        yield ['.'.join(before + [name])]

  def CmdGet(self, name):
    """Get the value of the given parameter."""
    return [[name, self.root.GetExport(name)]]

  def CmdSet(self, *args):
    """Set the given parameter(s) to the given value(s)."""
    groups = [(args[i], args[i+1]) for i in range(0, len(args), 2)]
    objects = set()
    ok = False
    try:
      for name, value in groups:
        objects.add(self.root.SetExportParam(name, value))
      ok = True
    finally:
      for o in objects:
        if ok:
          o.CommitTransaction()
        else:
          o.AbandonTransaction()
    return groups

  def _CmdList(self, name, recursive):
    it = self.root.ListExportsEx(name, recursive=recursive)
    for name, h, subname in it:
      if name.endswith('.'):
        yield [name]
      else:
        yield [name, h.GetExport(subname)]

  def CmdList(self, name=None):
    """Return a list of objects, non-recursively starting at the given name."""
    return self._CmdList(name, recursive=False)

  CmdLs = CmdList

  def CmdRlist(self, name=None):
    """Return a list of objects, recursively starting at the given name."""
    return self._CmdList(name, recursive=True)

  def CmdValidate(self, name=None):
    """Validate the schema of an object and its children."""
    h = self.root
    if name:
      h = self.root.Sub(name)
    h.ValidateExports(path=[name])
    return []

  def CmdAdd(self, name, idx=None):
    """Add a sub-object to the given list with the given (optional) index."""
    idx, unused_obj = self.root.AddExportObject(name, idx)
    return [[idx]]

  def CmdDel(self, name, *idxlist):
    """Delete one or more sub-objects from the given list."""
    if not idxlist:
      raise Exception('del needs >=2 parameters: list_name and indexes')
    for idx in idxlist:
      self.root.DeleteExportObject(name, idx)
      yield [idx]

  def CmdDownload(self, url):
    """Download a system image, install it, and reboot."""
    self.download_manager.NewDownload(
        command_key='rcmd',
        file_type='1 IMAGE',
        url=url,
        username=None,
        password=None,
        file_size=0,
        target_filename='rcmd.gi',
        delay_seconds=0)
    return [['OK', 'Starting download.']]

  def CmdWakeup(self):
    """Trigger an ACS session."""
    if self.state_machine is None:
      raise Exception('No state machine to wake up')
    self.state_machine.NewWakeupSession()
    return [['OK', 'Starting wakeup session.']]


def MakeRemoteCommandStreamer(root, state_machine):
  def Fn(sock, address):
    return RemoteCommandStreamer(sock, address, root, state_machine)
  return Fn


def main():
  loop = mainloop.MainLoop()

  class Sub(core.Exporter):

    def __init__(self):
      core.Exporter.__init__(self)
      self.Export(params=['Value'])
      self.Value = 0

  root = core.Exporter()
  root.Sub = Sub
  root.SubList = {}
  root.Test = 'this is a test string'
  root.Export(params=['Test'], lists=['Sub'])

  loop.ListenInet(('', 12999), MakeRemoteCommandStreamer(root, None))
  loop.ListenUnix('/tmp/cwmpd.sock', MakeRemoteCommandStreamer(root, None))
  loop.Start()


if __name__ == '__main__':
  main()

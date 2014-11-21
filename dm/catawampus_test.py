#!/usr/bin/python
# Copyright 2014 Google Inc. All Rights Reserved.
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

"""Unit tests for catawampus.py implementation."""

__author__ = 'dgentry@google.com (Denton Gentry)'

import google3
from tr.wvtest import unittest
import tr.core
import tr.experiment
import tr.handle
import catawampus


class CatawampusTest(unittest.TestCase):
  """Tests for catawampus.py."""

  def testValidateExports(self):
    r = tr.core.Exporter()
    h = tr.experiment.ExperimentHandle(r)
    c = catawampus.CatawampusDm(h)
    tr.handle.ValidateExports(c)

  def testRuntimeEnv(self):
    r = tr.core.Exporter()
    h = tr.experiment.ExperimentHandle(r)
    c = catawampus.CatawampusDm(h)
    self.assertTrue(c.RuntimeEnvInfo)

  def testProfiler(self):
    r = tr.core.Exporter()
    h = tr.experiment.ExperimentHandle(r)
    c = catawampus.CatawampusDm(h)
    c.Profiler.Enable = True
    # Profiler is running. Need something to profile.
    unused_j = 0
    for i in range(1000):
      unused_j += i
    c.Profiler.Enable = False
    # We don't check the content (too fragile for a test), just that it
    # generated *something*
    self.assertTrue(c.Profiler.Result)


if __name__ == '__main__':
  unittest.main()

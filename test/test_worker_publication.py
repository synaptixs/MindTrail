"""Atomic worker publication under transient Windows reader sharing and cancellation."""
import errno
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from analysis_worker import atomic_json,AnalysisCancelled


def sharing_error():
    error=PermissionError(errno.EACCES,'Synthetic Windows sharing violation');error.winerror=32;return error


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'status.json'
        atomic_json(self.path,{'state':'previous'})
    def test_transient_reader_conflict_preserves_atomic_json(self):
        real=Path.replace;attempts=[]
        def replace(source,destination):
            self.assertEqual(json.loads(self.path.read_bytes()),{'state':'previous'})
            attempts.append(1)
            if len(attempts)<3:raise sharing_error()
            return real(source,destination)
        with patch('analysis_worker.sys.platform','win32'),patch.object(Path,'replace',replace):atomic_json(self.path,{'state':'current'})
        self.assertEqual(json.loads(self.path.read_bytes()),{'state':'current'});self.assertEqual(len(attempts),3)
    def test_persistent_conflict_is_bounded_and_cancellation_keeps_prior(self):
        with patch('analysis_worker.sys.platform','win32'),patch.object(Path,'replace',side_effect=sharing_error()) as replacing,patch('analysis_worker.time.sleep'):
            with self.assertRaises(PermissionError):atomic_json(self.path,{'state':'new'})
            self.assertLessEqual(replacing.call_count,21)
        self.assertEqual(json.loads(self.path.read_bytes()),{'state':'previous'})
        canceled=threading.Event()
        def cancel():
            if canceled.is_set():raise AnalysisCancelled()
        def block(*_):canceled.set();raise sharing_error()
        with patch('analysis_worker.sys.platform','win32'),patch.object(Path,'replace',block):
            with self.assertRaises(AnalysisCancelled):atomic_json(self.path,{'state':'new'},cancel)
        self.assertEqual(json.loads(self.path.read_bytes()),{'state':'previous'})
    def test_other_io_failures_are_not_retried(self):
        with patch.object(Path,'replace',side_effect=OSError(errno.ENOSPC,'full disk')) as replacing:
            with self.assertRaises(OSError):atomic_json(self.path,{'state':'new'})
            self.assertEqual(replacing.call_count,1)
    @unittest.skipUnless(sys.platform=='win32','Native Windows sharing contract')
    def test_native_open_reader_releases_before_publication(self):
        probe=self.path.with_name('probe.json');other=self.path.with_name('replacement.json');probe.write_bytes(b'old');other.write_bytes(b'new')
        with probe.open('rb'):
            try:other.replace(probe)
            except PermissionError:pass
            else:self.skipTest('This Windows filesystem permits replacement while read-open; conflict precondition absent')
        errors=[];started=threading.Event()
        def write():
            try:atomic_json(self.path,{'state':'current'},started.set)
            except Exception as error:errors.append(error)
        with self.path.open('rb') as reader:
            worker=threading.Thread(target=write);worker.start();self.assertTrue(started.wait(2));time.sleep(.05)
            self.assertEqual(json.load(reader),{'state':'previous'})
        worker.join(3);self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.assertEqual(json.loads(self.path.read_bytes()),{'state':'current'})

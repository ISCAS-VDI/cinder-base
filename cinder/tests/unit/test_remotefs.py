#  Copyright 2014 Cloudbase Solutions Srl
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import copy
import os

import ddt
import mock

from cinder import exception
from cinder.image import image_utils
from cinder import test
from cinder import utils
from cinder.volume.drivers import remotefs


@ddt.ddt
class RemoteFsSnapDriverTestCase(test.TestCase):

    _FAKE_CONTEXT = 'fake_context'
    _FAKE_VOLUME_ID = '4f711859-4928-4cb7-801a-a50c37ceaccc'
    _FAKE_VOLUME_NAME = 'volume-%s' % _FAKE_VOLUME_ID
    _FAKE_VOLUME = {'id': _FAKE_VOLUME_ID,
                    'size': 1,
                    'provider_location': 'fake_share',
                    'name': _FAKE_VOLUME_NAME,
                    'status': 'available'}
    _FAKE_MNT_POINT = '/mnt/fake_hash'
    _FAKE_VOLUME_PATH = os.path.join(_FAKE_MNT_POINT,
                                     _FAKE_VOLUME_NAME)
    _FAKE_SNAPSHOT_ID = '5g811859-4928-4cb7-801a-a50c37ceacba'
    _FAKE_SNAPSHOT = {'context': _FAKE_CONTEXT,
                      'id': _FAKE_SNAPSHOT_ID,
                      'volume': _FAKE_VOLUME,
                      'volume_id': _FAKE_VOLUME_ID,
                      'status': 'available',
                      'volume_size': 1}
    _FAKE_SNAPSHOT_PATH = (_FAKE_VOLUME_PATH + '.' + _FAKE_SNAPSHOT_ID)

    def setUp(self):
        super(RemoteFsSnapDriverTestCase, self).setUp()
        self._driver = remotefs.RemoteFSSnapDriver()
        self._driver._remotefsclient = mock.Mock()
        self._driver._execute = mock.Mock()
        self._driver._delete = mock.Mock()

    def _test_delete_snapshot(self, volume_in_use=False,
                              stale_snapshot=False,
                              is_active_image=True):
        # If the snapshot is not the active image, it is guaranteed that
        # another snapshot exists having it as backing file.

        fake_snapshot_name = os.path.basename(self._FAKE_SNAPSHOT_PATH)
        fake_info = {'active': fake_snapshot_name,
                     self._FAKE_SNAPSHOT['id']: fake_snapshot_name}
        fake_snap_img_info = mock.Mock()
        fake_base_img_info = mock.Mock()
        if stale_snapshot:
            fake_snap_img_info.backing_file = None
        else:
            fake_snap_img_info.backing_file = self._FAKE_VOLUME_NAME
        fake_snap_img_info.file_format = 'qcow2'
        fake_base_img_info.backing_file = None
        fake_base_img_info.file_format = 'raw'

        self._driver._local_path_volume_info = mock.Mock(
            return_value=mock.sentinel.fake_info_path)
        self._driver._qemu_img_info = mock.Mock(
            side_effect=[fake_snap_img_info, fake_base_img_info])
        self._driver._local_volume_dir = mock.Mock(
            return_value=self._FAKE_MNT_POINT)

        self._driver._read_info_file = mock.Mock()
        self._driver._write_info_file = mock.Mock()
        self._driver._img_commit = mock.Mock()
        self._driver._rebase_img = mock.Mock()
        self._driver._ensure_share_writable = mock.Mock()
        self._driver._delete_stale_snapshot = mock.Mock()
        self._driver._delete_snapshot_online = mock.Mock()

        expected_info = {
            'active': fake_snapshot_name,
            self._FAKE_SNAPSHOT_ID: fake_snapshot_name
        }

        if volume_in_use:
            fake_snapshot = copy.deepcopy(self._FAKE_SNAPSHOT)
            fake_snapshot['volume']['status'] = 'in-use'

            self._driver._read_info_file.return_value = fake_info

            self._driver._delete_snapshot(fake_snapshot)
            if stale_snapshot:
                self._driver._delete_stale_snapshot.assert_called_once_with(
                    fake_snapshot)
            else:
                expected_online_delete_info = {
                    'active_file': fake_snapshot_name,
                    'snapshot_file': fake_snapshot_name,
                    'base_file': self._FAKE_VOLUME_NAME,
                    'base_id': None,
                    'new_base_file': None
                }
                self._driver._delete_snapshot_online.assert_called_once_with(
                    self._FAKE_CONTEXT, fake_snapshot,
                    expected_online_delete_info)

        elif is_active_image:
            self._driver._read_info_file.return_value = fake_info

            self._driver._delete_snapshot(self._FAKE_SNAPSHOT)

            self._driver._img_commit.assert_called_once_with(
                self._FAKE_SNAPSHOT_PATH)
            self.assertNotIn(self._FAKE_SNAPSHOT_ID, fake_info)
            self._driver._write_info_file.assert_called_once_with(
                mock.sentinel.fake_info_path, fake_info)
        else:
            fake_upper_snap_id = 'fake_upper_snap_id'
            fake_upper_snap_path = (
                self._FAKE_VOLUME_PATH + '-snapshot' + fake_upper_snap_id)
            fake_upper_snap_name = os.path.basename(fake_upper_snap_path)

            fake_backing_chain = [
                {'filename': fake_upper_snap_name,
                 'backing-filename': fake_snapshot_name},
                {'filename': fake_snapshot_name,
                 'backing-filename': self._FAKE_VOLUME_NAME},
                {'filename': self._FAKE_VOLUME_NAME,
                 'backing-filename': None}]

            fake_info[fake_upper_snap_id] = fake_upper_snap_name
            fake_info[self._FAKE_SNAPSHOT_ID] = fake_snapshot_name
            fake_info['active'] = fake_upper_snap_name

            expected_info = copy.deepcopy(fake_info)
            del expected_info[self._FAKE_SNAPSHOT_ID]

            self._driver._read_info_file.return_value = fake_info
            self._driver._get_backing_chain_for_path = mock.Mock(
                return_value=fake_backing_chain)

            self._driver._delete_snapshot(self._FAKE_SNAPSHOT)

            self._driver._img_commit.assert_called_once_with(
                self._FAKE_SNAPSHOT_PATH)
            self._driver._rebase_img.assert_called_once_with(
                fake_upper_snap_path, self._FAKE_VOLUME_NAME,
                fake_base_img_info.file_format)
            self._driver._write_info_file.assert_called_once_with(
                mock.sentinel.fake_info_path, expected_info)

    def test_delete_snapshot_when_active_file(self):
        self._test_delete_snapshot()

    def test_delete_snapshot_in_use(self):
        self._test_delete_snapshot(volume_in_use=True)

    def test_delete_snapshot_in_use_stale_snapshot(self):
        self._test_delete_snapshot(volume_in_use=True,
                                   stale_snapshot=True)

    def test_delete_snapshot_with_one_upper_file(self):
        self._test_delete_snapshot(is_active_image=False)

    def test_delete_stale_snapshot(self):
        fake_snapshot_name = os.path.basename(self._FAKE_SNAPSHOT_PATH)
        fake_snap_info = {
            'active': self._FAKE_VOLUME_NAME,
            self._FAKE_SNAPSHOT_ID: fake_snapshot_name
        }
        expected_info = {'active': self._FAKE_VOLUME_NAME}

        self._driver._local_path_volume_info = mock.Mock(
            return_value=mock.sentinel.fake_info_path)
        self._driver._read_info_file = mock.Mock(
            return_value=fake_snap_info)
        self._driver._local_volume_dir = mock.Mock(
            return_value=self._FAKE_MNT_POINT)
        self._driver._write_info_file = mock.Mock()

        self._driver._delete_stale_snapshot(self._FAKE_SNAPSHOT)

        self._driver._delete.assert_called_once_with(self._FAKE_SNAPSHOT_PATH)
        self._driver._write_info_file.assert_called_once_with(
            mock.sentinel.fake_info_path, expected_info)

    def test_do_create_snapshot(self):
        self._driver._local_volume_dir = mock.Mock(
            return_value=self._FAKE_VOLUME_PATH)
        fake_backing_path = os.path.join(
            self._driver._local_volume_dir(),
            self._FAKE_VOLUME_NAME)

        self._driver._execute = mock.Mock()
        self._driver._set_rw_permissions = mock.Mock()
        self._driver._qemu_img_info = mock.Mock(
            return_value=mock.Mock(file_format=mock.sentinel.backing_fmt))

        self._driver._do_create_snapshot(self._FAKE_SNAPSHOT,
                                         self._FAKE_VOLUME_NAME,
                                         self._FAKE_SNAPSHOT_PATH)
        command1 = ['qemu-img', 'create', '-f', 'qcow2', '-o',
                    'backing_file=%s' % fake_backing_path,
                    self._FAKE_SNAPSHOT_PATH]
        command2 = ['qemu-img', 'rebase', '-u',
                    '-b', self._FAKE_VOLUME_NAME,
                    '-F', mock.sentinel.backing_fmt,
                    self._FAKE_SNAPSHOT_PATH]

        self._driver._execute.assert_any_call(*command1, run_as_root=True)
        self._driver._execute.assert_any_call(*command2, run_as_root=True)

    def _test_create_snapshot(self, volume_in_use=False):
        fake_snapshot = copy.deepcopy(self._FAKE_SNAPSHOT)
        fake_snapshot_info = {}
        fake_snapshot_file_name = os.path.basename(self._FAKE_SNAPSHOT_PATH)

        self._driver._local_path_volume_info = mock.Mock(
            return_value=mock.sentinel.fake_info_path)
        self._driver._read_info_file = mock.Mock(
            return_value=fake_snapshot_info)
        self._driver._do_create_snapshot = mock.Mock()
        self._driver._create_snapshot_online = mock.Mock()
        self._driver._write_info_file = mock.Mock()
        self._driver.get_active_image_from_info = mock.Mock(
            return_value=self._FAKE_VOLUME_NAME)
        self._driver._get_new_snap_path = mock.Mock(
            return_value=self._FAKE_SNAPSHOT_PATH)

        expected_snapshot_info = {
            'active': fake_snapshot_file_name,
            self._FAKE_SNAPSHOT_ID: fake_snapshot_file_name
        }

        if volume_in_use:
            fake_snapshot['volume']['status'] = 'in-use'
            expected_method_called = '_create_snapshot_online'
        else:
            fake_snapshot['volume']['status'] = 'available'
            expected_method_called = '_do_create_snapshot'

        self._driver._create_snapshot(fake_snapshot)

        fake_method = getattr(self._driver, expected_method_called)
        fake_method.assert_called_with(
            fake_snapshot, self._FAKE_VOLUME_NAME,
            self._FAKE_SNAPSHOT_PATH)
        self._driver._write_info_file.assert_called_with(
            mock.sentinel.fake_info_path,
            expected_snapshot_info)

    def test_create_snapshot_volume_available(self):
        self._test_create_snapshot()

    def test_create_snapshot_volume_in_use(self):
        self._test_create_snapshot(volume_in_use=True)

    def test_create_snapshot_invalid_volume(self):
        fake_snapshot = copy.deepcopy(self._FAKE_SNAPSHOT)
        fake_snapshot['volume']['status'] = 'error'

        self.assertRaises(exception.InvalidVolume,
                          self._driver._create_snapshot,
                          fake_snapshot)

    @mock.patch('cinder.db.snapshot_get')
    @mock.patch('time.sleep')
    def test_create_snapshot_online_with_concurrent_delete(
            self, mock_sleep, mock_snapshot_get):
        self._driver._nova = mock.Mock()

        # Test what happens when progress is so slow that someone
        # decides to delete the snapshot while the last known status is
        # "creating".
        mock_snapshot_get.side_effect = [
            {'status': 'creating', 'progress': '42%'},
            {'status': 'creating', 'progress': '45%'},
            {'status': 'deleting'},
        ]

        with mock.patch.object(self._driver, '_do_create_snapshot') as \
                mock_do_create_snapshot:
            self.assertRaises(exception.RemoteFSConcurrentRequest,
                              self._driver._create_snapshot_online,
                              self._FAKE_SNAPSHOT,
                              self._FAKE_VOLUME_NAME,
                              self._FAKE_SNAPSHOT_PATH)

        mock_do_create_snapshot.assert_called_once_with(
            self._FAKE_SNAPSHOT, self._FAKE_VOLUME_NAME,
            self._FAKE_SNAPSHOT_PATH)
        self.assertEqual([mock.call(1), mock.call(1)],
                         mock_sleep.call_args_list)
        self.assertEqual(3, mock_snapshot_get.call_count)
        mock_snapshot_get.assert_called_with(self._FAKE_SNAPSHOT['context'],
                                             self._FAKE_SNAPSHOT['id'])

    @mock.patch.object(utils, 'synchronized')
    def _locked_volume_operation_test_helper(self, mock_synchronized, func,
                                             expected_exception=False,
                                             *args, **kwargs):
        def mock_decorator(*args, **kwargs):
            def mock_inner(f):
                return f
            return mock_inner

        mock_synchronized.side_effect = mock_decorator
        expected_lock = '%s-%s' % (self._driver.driver_prefix,
                                   self._FAKE_VOLUME_ID)

        if expected_exception:
            self.assertRaises(expected_exception, func,
                              self._driver,
                              *args, **kwargs)
        else:
            ret_val = func(self._driver, *args, **kwargs)

            mock_synchronized.assert_called_with(expected_lock,
                                                 external=False)
            self.assertEqual(mock.sentinel.ret_val, ret_val)

    def test_locked_volume_id_operation(self):
        mock_volume = {'id': self._FAKE_VOLUME_ID}

        @remotefs.locked_volume_id_operation
        def synchronized_func(inst, volume):
            return mock.sentinel.ret_val

        self._locked_volume_operation_test_helper(func=synchronized_func,
                                                  volume=mock_volume)

    def test_locked_volume_id_snapshot_operation(self):
        mock_snapshot = {'volume': {'id': self._FAKE_VOLUME_ID}}

        @remotefs.locked_volume_id_operation
        def synchronized_func(inst, snapshot):
            return mock.sentinel.ret_val

        self._locked_volume_operation_test_helper(func=synchronized_func,
                                                  snapshot=mock_snapshot)

    def test_locked_volume_id_operation_exception(self):
        @remotefs.locked_volume_id_operation
        def synchronized_func(inst):
            return mock.sentinel.ret_val

        self._locked_volume_operation_test_helper(
            func=synchronized_func,
            expected_exception=exception.VolumeBackendAPIException)

    @mock.patch.object(image_utils, 'qemu_img_info')
    @mock.patch('os.path.basename')
    def _test_qemu_img_info(self, mock_basename,
                            mock_qemu_img_info, backing_file, basedir,
                            valid_backing_file=True):
        fake_vol_name = 'fake_vol_name'
        mock_info = mock_qemu_img_info.return_value
        mock_info.image = mock.sentinel.image_path
        mock_info.backing_file = backing_file

        self._driver._VALID_IMAGE_EXTENSIONS = ['vhd', 'vhdx', 'raw', 'qcow2']

        mock_basename.side_effect = [mock.sentinel.image_basename,
                                     mock.sentinel.backing_file_basename]

        if valid_backing_file:
            img_info = self._driver._qemu_img_info_base(
                mock.sentinel.image_path, fake_vol_name, basedir)
            self.assertEqual(mock_info, img_info)
            self.assertEqual(mock.sentinel.image_basename,
                             mock_info.image)
            expected_basename_calls = [mock.call(mock.sentinel.image_path)]
            if backing_file:
                self.assertEqual(mock.sentinel.backing_file_basename,
                                 mock_info.backing_file)
                expected_basename_calls.append(mock.call(backing_file))
            mock_basename.assert_has_calls(expected_basename_calls)
        else:
            self.assertRaises(exception.RemoteFSException,
                              self._driver._qemu_img_info_base,
                              mock.sentinel.image_path,
                              fake_vol_name, basedir)

        mock_qemu_img_info.assert_called_with(mock.sentinel.image_path)

    @ddt.data([None, '/fake_basedir'],
              ['/fake_basedir/cb2016/fake_vol_name', '/fake_basedir'],
              ['/fake_basedir/cb2016/fake_vol_name.vhd', '/fake_basedir'],
              ['/fake_basedir/cb2016/fake_vol_name.404f-404',
               '/fake_basedir'],
              ['/fake_basedir/cb2016/fake_vol_name.tmp-snap-404f-404',
               '/fake_basedir'])
    @ddt.unpack
    def test_qemu_img_info_valid_backing_file(self, backing_file, basedir):
        self._test_qemu_img_info(backing_file=backing_file,
                                 basedir=basedir)

    @ddt.data(['/other_random_path', '/fake_basedir'],
              ['/other_basedir/cb2016/fake_vol_name', '/fake_basedir'],
              ['/fake_basedir/invalid_hash/fake_vol_name', '/fake_basedir'],
              ['/fake_basedir/cb2016/invalid_vol_name', '/fake_basedir'],
              ['/fake_basedir/cb2016/fake_vol_name.info', '/fake_basedir'],
              ['/fake_basedir/cb2016/fake_vol_name-random-suffix',
               '/fake_basedir'],
              ['/fake_basedir/cb2016/fake_vol_name.invalidext',
               '/fake_basedir'])
    @ddt.unpack
    def test_qemu_img_info_invalid_backing_file(self, backing_file, basedir):
        self._test_qemu_img_info(backing_file=backing_file,
                                 basedir=basedir,
                                 valid_backing_file=False)

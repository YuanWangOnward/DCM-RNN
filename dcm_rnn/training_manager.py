import random
import numpy as np
import scipy.ndimage
import pandas as pd
import math as mth
import scipy as sp
import scipy.stats
import os
import sys
import warnings
import collections
import matplotlib.pyplot as plt
import subprocess
import pickle
import copy
import toolboxes as tb
import tf_model as tfm
import tensorflow as tf
import copy
from operator import attrgetter


class DistributedDataPackage:
    def __init__(self):
        pass


class TrainingManager(tb.Initialization):
    """
    This class is used to the manage training process
    """

    def __init__(self):
        tb.Initialization.__init__(self)

        # global setting
        self.IF_RANDOM_H_PARA = False
        self.IF_RANDOM_H_STATE_INIT = False
        self.IF_NOISED_Y = True

        self.IF_NODE_MODE = False
        self.IF_IMAGE_LOG = True
        self.IF_DATA_LOG = True

        self.SNR = 3
        self.NODE_INDEX = 0
        self.SMOOTH_WEIGHT = 0.4
        self.N_RECURRENT_STEP = 64
        self.MAX_EPOCHS = 4

        self.MAX_EPOCHS_INNER = 4
        self.N_SEGMENTS = 128  # total amount of self.spm_data segments
        # self.CHECK_STEPS = 4
        self.CHECK_STEPS = self.N_SEGMENTS * self.MAX_EPOCHS_INNER
        self.LEARNING_RATE = 128 / self.N_RECURRENT_STEP
        self.DATA_SHIFT = 4

        self.PACKAGE_LABEL = ''  # used in parallel processing
        self.LOG_EXTRA_PREFIX = ''
        self.IMAGE_LOG_DIR = './image_logs/'
        self.DATA_LOG_DIR = './data_logs/'
        self.N_CORES = 4

        self.N_PACKAGES = 8

        self.data = {'total_iteration_count': 0}

    def __dir__(self):
        return ['IF_RANDOM_H_PARA', 'IF_RANDOM_H_STATE_INIT', 'IF_NOISED_Y',
                'IF_NODE_MODE', 'IF_IMAGE_LOG', 'IF_DATA_LOG',
                'SNR', 'NODE_INDEX', 'SMOOTH_WEIGHT', 'N_RECURRENT_STEP', 'MAX_EPOCHS',
                'MAX_EPOCHS_INNER', 'n_segments', 'CHECK_STEPS', 'LEARNING_RATE', 'DATA_SHIFT',
                'PACKAGE_LABEL', 'LOG_EXTRA_PREFIX', 'IMAGE_LOG_DIR', 'DATA_LOG_DIR', 'N_CORES',
                'N_PACKAGES',
                'spm_data'
                ]

    def prepare_dcm_rnn(self, dr, tag='initializer'):
        """
        Set parameters in dr with training manager configures. Modify dr in place.
        :param dr: a DcmRnn instance, which will be used to build tensorflow model
        :param tag: experiment type
        :return: modified dr
        """
        dr.learning_rate = self.LEARNING_RATE
        dr.shift_data = self.DATA_SHIFT
        dr.n_recurrent_step = self.N_RECURRENT_STEP

        if tag == 'initializer':
            dr.loss_weighting['smooth'] = self.SMOOTH_WEIGHT
            if self.IF_NODE_MODE:
                dr.n_region = 1
            for key in dr.trainable_flags.keys():
                # in the initialization graph, the hemodynamic parameters are not trainable
                dr.trainable_flags[key] = False
        return dr

    '''
    def prepare_data(self, du, dr):
        """
        Prepare spm_data in du for dr in training. Create a 'spm_data' dictionary 
        :param du: a DataUnit instance, with needed spm_data
        :param dr: a DcmRnn instance, with needed parameters
        :return: 
        """

        # create spm_data according to flag
        if self.IF_RANDOM_H_PARA:
            self.H_PARA_INITIAL = \
                self.randomly_generate_hemodynamic_parameters(dr.n_region, deviation_constraint=2).astype(np.float32)
        else:
            self.H_PARA_INITIAL = self.get_standard_hemodynamic_parameters(n_node=dr.n_region).astype(np.float32)

        if self.IF_RANDOM_H_STATE_INIT:
            self.H_STATE_INITIAL = du.get('h')[random.randint(64, du.get('h').shape[0] - 64)].astype(np.float32)
        else:
            self.H_STATE_INITIAL = \
                self.set_initial_hemodynamic_state_as_inactivated(n_node=dr.n_region).astype(np.float32)

        if self.IF_NOISED_Y:
            std = np.std(du.get('y').reshape([-1])) / self.SNR
            self.NOISE = np.random.normal(0, std, du.get('y').shape)
        else:
            self.NOISE = np.zeros(du.get('y').shape)

        self.spm_data['y_train'] = \
            tb.split(du.get('y') + self.NOISE, dr.n_recurrent_step, dr.shift_data, dr.shift_x_y)
        max_segments_natural = len(self.spm_data['y_train'])
        self.spm_data['max_segments_natural'] = max_segments_natural
        self.spm_data['y_true'] = \
            tb.split(du.get('y'), dr.n_recurrent_step, dr.shift_data, dr.shift_x_y)[:max_segments_natural]
        self.spm_data['h_true_monitor'] \
            = tb.split(du.get('h'), dr.n_recurrent_step, dr.shift_data)[:max_segments_natural]
        self.spm_data['x_true'] = tb.split(du.get('x'), dr.n_recurrent_step, dr.shift_data)[:max_segments_natural]
        self.spm_data['u'] = tb.split(du.get('u'), dr.n_recurrent_step, dr.shift_data)[:max_segments_natural]

        if self.n_segments is not None:
            if self.n_segments > max_segments_natural:
                self.n_segments = max_segments_natural
                warnings.warn("self.n_segments is larger than the length of available spm_data", UserWarning)
            else:
                self.spm_data['u'] = self.spm_data['u'][:self.n_segments]
                self.spm_data['x_true'] = self.spm_data['x_true'][:self.n_segments]
                self.spm_data['h_true_monitor'] = self.spm_data['h_true_monitor'][:self.n_segments]
                self.spm_data['y_true'] = self.spm_data['y_true'][:self.n_segments]
                self.spm_data['y_train'] = self.spm_data['y_train'][:self.n_segments]

        if self.IF_NODE_MODE is True:
            node_index = self.NODE_INDEX
            self.spm_data['x_true'] = [array[:, node_index].reshape(dr.n_recurrent_step, 1) for array in self.spm_data['x_true']]
            self.spm_data['h_true_monitor'] = [np.take(array, node_index, 1) for array in self.spm_data['h_true_monitor']]
            self.spm_data['y_true'] = [array[:, node_index].reshape(dr.n_recurrent_step, 1) for array in self.spm_data['y_true']]
            self.spm_data['y_train'] = [array[:, node_index].reshape(dr.n_recurrent_step, 1) for array in self.spm_data['y_train']]
            self.H_STATE_INITIAL = self.H_STATE_INITIAL[node_index].reshape(1, 4)

        # saved self.SEQUENCE_LENGTH = dr.n_recurrent_step + (len(self.spm_data['x_true']) - 1) * dr.shift_data
        # collect merged self.spm_data (without split and merge, it can be tricky to cut proper part from du)
        self.spm_data['u_merged'] = tb.merge(self.spm_data['u'], dr.n_recurrent_step, dr.shift_data)
        self.spm_data['x_true_merged'] = tb.merge(self.spm_data['x_true'], dr.n_recurrent_step, dr.shift_data)
        # x_hat is with extra wrapper for easy modification with a single index
        self.spm_data['x_hat_merged'] = \
            tb.ArrayWrapper(np.zeros(self.spm_data['x_true_merged'].shape), dr.n_recurrent_step, dr.shift_data)
        self.spm_data['h_true_monitor_merged'] = \
            tb.merge(self.spm_data['h_true_monitor'], dr.n_recurrent_step, dr.shift_data)
        self.spm_data['y_true_merged'] = tb.merge(self.spm_data['y_true'], dr.n_recurrent_step, dr.shift_data)
        self.spm_data['y_train_merged'] = tb.merge(self.spm_data['y_train'], dr.n_recurrent_step, dr.shift_data)

        # run forward pass with x_true to show y error caused by error in the network parameters
        with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())
            y_hat_x_true_log, h_hat_x_true_monitor_log, h_hat_x_true_connector_log = \
                dr.run_initializer_graph(sess, self.H_STATE_INITIAL, self.spm_data['x_true'])
        self.spm_data['h_hat_x_true_monitor'] = h_hat_x_true_monitor_log
        self.spm_data['y_hat_x_true'] = y_hat_x_true_log
        self.spm_data['h_hat_x_true_monitor_merged'] = \
            tb.merge(h_hat_x_true_monitor_log, dr.n_recurrent_step, dr.shift_data)
        self.spm_data['y_hat_x_true_merged'] = tb.merge(y_hat_x_true_log, dr.n_recurrent_step, dr.shift_data)

        self.spm_data['loss_x_normalizer'] = np.sum(self.spm_data['x_true_merged'].flatten() ** 2)
        self.spm_data['loss_y_normalizer'] = np.sum(self.spm_data['y_true_merged'].flatten() ** 2)
        self.spm_data['loss_smooth_normalizer'] = np.std(self.spm_data['x_true_merged'].flatten()) ** 2

        return self.spm_data
    '''

    def prepare_distributed_configure_package(self, package_number=None):
        """
        Pack up configure settings from TrainingManager in to several separate packages for parallel processing
        :param package_number: total amount of packages 
        :return: 
        """
        package_number = package_number or self.N_PACKAGES
        ddp = DistributedDataPackage()
        for name in dir(self):
            setattr(ddp, name, copy.deepcopy(getattr(self, name)))
        packages = [copy.deepcopy(ddp) for _ in range(package_number)]
        for idx, ddp in enumerate(packages):
            ddp.PACKAGE_LABEL = 'package_' + str(idx)
        return packages

    def modify_configure_packages(self, data_package, attribute, values):
        """
        Modify the attribute in data_package with value. 
        Update PACKAGE_LABEL and LOG_EXTRA_PREFIX attribute accordingly.
        If data_package is a DistributedDataPackage instance, it's duplicated as many as elements in values.
        If data_package is a list of DistributedDataPackage instances, 
        the value of the attribute in each instance is modified according to values.
        :param data_package: DistributedDataPackage instance, or a list of DistributedDataPackage instances
        :param attribute: target attribute
        :param values: a list of attribute values
        :return: a list of DistributedDataPackage instances with modified attribute
        """
        if isinstance(data_package, DistributedDataPackage):
            data_package_list = [copy.deepcopy(data_package) for _ in range(len(values))]
        else:
            if len(data_package) == len(values):
                data_package_list = data_package
            elif len(data_package) > len(values):
                warnings.warn(
                    "Data_package length is larger than provided attribute values. Tailing ones are not modified")
                data_package_list = data_package
            elif len(data_package) < len(values):
                warnings.warn(
                    "Data_package length is smaller than provided attribute values. "
                    "Extra spm_data packages are added, copied from the last data_package element.")
                data_package_list = data_package + [copy.deepcopy(data_package[-1])
                                                    for _ in range(len(values) - len(data_package))]
        for idx, dp in enumerate(data_package_list):
            assert attribute in dir(dp)
            setattr(dp, attribute, values[idx])
            if attribute not in ['LOG_EXTRA_PREFIX', 'PACKAGE_LABEL']:
                dp.LOG_EXTRA_PREFIX = dp.LOG_EXTRA_PREFIX + attribute + str(values[idx]) + '_'
            dp.PACKAGE_LABEL = 'package_' + str(idx)
        return data_package_list

    def prepare_data(self, du, dr, data_package):
        """
        Prepare spm_data in du for dr in training. Create a 'spm_data' dictionary
        :param du: a DataUnit instance, with needed spm_data
        :param dr: a DcmRnn instance, with needed parameters
        :param data_package: a dictionary, stores configure and spm_data for a particular experimental case
        :return: modified
        """
        dp = data_package
        data = dp.data

        # create spm_data according to flag
        if dp.IF_RANDOM_H_PARA:
            data['H_PARA_INITIAL'] = \
                dr.randomly_generate_hemodynamic_parameters(dr.n_region, deviation_constraint=2).astype(np.float32)
        else:
            data['H_PARA_INITIAL'] = dr.get_standard_hemodynamic_parameters(n_node=dr.n_region).astype(np.float32)

        if dp.IF_RANDOM_H_STATE_INIT:
            data['H_STATE_INITIAL'] = du.get('h')[random.randint(64, du.get('h').shape[0] - 64)].astype(np.float32)
        else:
            data['H_STATE_INITIAL'] = \
                dr.set_initial_hemodynamic_state_as_inactivated(n_node=dr.n_region).astype(np.float32)

        if dp.IF_NOISED_Y:
            std = np.std(du.get('y').reshape([-1])) / dp.SNR
            data['NOISE'] = np.random.normal(0, std, du.get('y').shape)
        else:
            data['NOISE'] = np.zeros(du.get('y').shape)

        data['y_train'] = tb.split(du.get('y') + data['NOISE'], dr.n_recurrent_step, dr.shift_data, dr.shift_x_y)
        max_segments_natural = len(data['y_train'])
        data['max_segments_natural'] = max_segments_natural
        data['y_true'] = tb.split(du.get('y'), dr.n_recurrent_step, dr.shift_data, dr.shift_x_y)[:max_segments_natural]
        data['h_true_monitor'] = tb.split(du.get('h'), dr.n_recurrent_step, dr.shift_data)[:max_segments_natural]
        data['x_true'] = tb.split(du.get('x'), dr.n_recurrent_step, dr.shift_data)[:max_segments_natural]
        data['u'] = tb.split(du.get('u'), dr.n_recurrent_step, dr.shift_data)[:max_segments_natural]

        if dp.N_SEGMENTS is not None:
            if dp.N_SEGMENTS > max_segments_natural:
                dp.N_SEGMENTS = max_segments_natural
                warnings.warn("dp.n_segments is larger than the length of available spm_data", UserWarning)
            else:
                data['u'] = data['u'][:dp.N_SEGMENTS]
                data['x_true'] = data['x_true'][:dp.N_SEGMENTS]
                data['h_true_monitor'] = data['h_true_monitor'][:dp.N_SEGMENTS]
                data['y_true'] = data['y_true'][:dp.N_SEGMENTS]
                data['y_train'] = data['y_train'][:dp.N_SEGMENTS]

        if dp.IF_NODE_MODE is True:
            node_index = dp.NODE_INDEX
            data['x_true'] = [array[:, node_index].reshape(dr.n_recurrent_step, 1) for array in data['x_true']]
            data['h_true_monitor'] = [np.take(array, node_index, 1) for array in data['h_true_monitor']]
            data['y_true'] = [array[:, node_index].reshape(dr.n_recurrent_step, 1) for array in data['y_true']]
            data['y_train'] = [array[:, node_index].reshape(dr.n_recurrent_step, 1) for array in data['y_train']]
            data['H_STATE_INITIAL'] = data['H_STATE_INITIAL'][node_index].reshape(1, 4)

        # saved dp.SEQUENCE_LENGTH = dr.n_recurrent_step + (len(spm_data['x_true']) - 1) * dr.shift_data
        # collect merged spm_data (without split and merge, it can be tricky to cut proper part from du)
        data['u_merged'] = tb.merge(data['u'], dr.n_recurrent_step, dr.shift_data)
        data['x_true_merged'] = tb.merge(data['x_true'], dr.n_recurrent_step, dr.shift_data)
        # x_hat is with extra wrapper for easy modification with a single index
        data['x_hat_merged'] = \
            tb.ArrayWrapper(np.zeros(data['x_true_merged'].shape), dr.n_recurrent_step, dr.shift_data)
        data['h_true_monitor_merged'] = \
            tb.merge(data['h_true_monitor'], dr.n_recurrent_step, dr.shift_data)
        data['y_true_merged'] = tb.merge(data['y_true'], dr.n_recurrent_step, dr.shift_data)
        data['y_train_merged'] = tb.merge(data['y_train'], dr.n_recurrent_step, dr.shift_data)

        return data_package

    def modify_signel_data_package(self, data_package, key, value):
        """
        Modify the item in data_package.spm_data with value.
        Update LOG_EXTRA_PREFIX attribute accordingly.
        :param data_package: DistributedDataPackage instance
        :param key: target spm_data key
        :param value: one value for target key
        :return: a modified DistributedDataPackage instances
        """
        data_package.data[key] = copy.deepcopy(value)
        data_package.LOG_EXTRA_PREFIX = data_package.LOG_EXTRA_PREFIX + key + '_modified_'
        return data_package

    def modify_data_packages(self, data_package, key, values):
        """
        Modify the item in data_package.spm_data with value.
        Update LOG_EXTRA_PREFIX attribute accordingly.
        If data_package is a DistributedDataPackage instance, it's duplicated as many as elements in values.
        If data_package is a list of DistributedDataPackage instances, 
        the value of the item in each instance.spm_data is modified according to values.
        :param data_package: DistributedDataPackage instance, or a list of DistributedDataPackage instances
        :param key: target spm_data key
        :param values: one value or a list of attribute values
        :return: a list of DistributedDataPackage instances with modified attribute
        """
        if isinstance(data_package, DistributedDataPackage):
            data_package_list = [copy.deepcopy(data_package) for _ in range(len(values))]
        else:
            if len(data_package) == len(values):
                data_package_list = data_package
            elif len(data_package) > len(values):
                warnings.warn(
                    "Data_package length is larger than provided attribute values. Tailing ones are not modified")
                data_package_list = data_package
            elif len(data_package) < len(values):
                warnings.warn(
                    "Data_package length is smaller than provided attribute values. "
                    "Extra spm_data packages are added, copied from the last data_package element.")
                data_package_list = data_package + [copy.deepcopy(data_package[-1])
                                                    for _ in range(len(values) - len(data_package))]
        for idx, dp in enumerate(data_package_list):
            dp.data[key] = copy.deepcopy(values[idx])
            dp.LOG_EXTRA_PREFIX = dp.LOG_EXTRA_PREFIX + key + '_modified_'
        return data_package_list

    def get_log_prefix(self, data_package):
        dp = data_package
        count_total = dp.data['total_iteration_count']
        extra_prefix = '_'.join([dp.PACKAGE_LABEL, dp.LOG_EXTRA_PREFIX])
        if dp.IF_NODE_MODE:
            node_index = dp.NODE_INDEX
        else:
            node_index = 'a'
        prefix = extra_prefix \
                 + '_nNode' + str(node_index) \
                 + '_nSeg' + str(dp.N_SEGMENTS) \
                 + '_nRec' + str(dp.N_RECURRENT_STEP) \
                 + '_nDaSh' + str(dp.DATA_SHIFT) \
                 + '_leRa' + str(dp.LEARNING_RATE).replace('.', 'p') \
                 + '_iter' + str(count_total)
        return prefix

    def calculate_log_data(self, dr, data_package, isess):
        """"""
        dp = data_package
        data = dp.data

        if 'y_hat_x_true' not in data.keys():
            # run forward pass with x_true to show y error caused by error in the network parameters
            isess.run(tf.global_variables_initializer())
            y_hat_x_true_log, h_hat_x_true_monitor_log, h_hat_x_true_connector_log = \
                dr.run_initializer_graph(isess, data['H_STATE_INITIAL'], data['x_true'])

            data['h_hat_x_true_monitor'] = h_hat_x_true_monitor_log
            data['y_hat_x_true'] = y_hat_x_true_log
            data['h_hat_x_true_monitor_merged'] = tb.merge(h_hat_x_true_monitor_log, dr.n_recurrent_step, dr.shift_data)
            data['y_hat_x_true_merged'] = tb.merge(y_hat_x_true_log, dr.n_recurrent_step, dr.shift_data)

            data['loss_x_normalizer'] = np.sum(data['x_true_merged'].flatten() ** 2)
            data['loss_y_normalizer'] = np.sum(data['y_true_merged'].flatten() ** 2)
            data['loss_smooth_normalizer'] = np.std(data['x_true_merged'].flatten()) ** 2

        data['x_hat'] = tb.split(data['x_hat_merged'].get(), n_segment=dr.n_recurrent_step, n_step=dr.shift_data)
        if dp.IF_NODE_MODE:
            data['x_hat'] = [array.reshape(dr.n_recurrent_step, 1) for array in data['x_hat']]

        isess.run(tf.global_variables_initializer())
        y_hat_log, h_hat_monitor_log, h_hat_connector_log = \
            dr.run_initializer_graph(isess, dp.data['H_STATE_INITIAL'], data['x_hat'])

        # collect results
        # segmented spm_data
        data['x_hat'] = data['x_hat']
        data['x_true'] = data['x_true']

        data['h_true_monitor'] = data['h_true_monitor']
        data['h_hat_x_true_monitor'] = data['h_hat_x_true_monitor']
        data['h_hat_monitor'] = h_hat_monitor_log

        data['y_train'] = data['y_train']
        data['y_true'] = data['y_true']
        data['y_hat_x_true'] = data['y_hat_x_true']
        data['y_hat'] = y_hat_log

        # merged spm_data
        data['x_true_merged'] = data['x_true_merged']
        data['x_hat_merged'] = data['x_hat_merged']

        data['h_true_monitor_merged'] = data['h_true_monitor_merged']
        data['h_hat_x_true_monitor_merged'] = data['h_hat_x_true_monitor_merged']
        data['h_hat_monitor_merged'] = tb.merge(h_hat_monitor_log, dr.n_recurrent_step, dr.shift_data)

        data['y_train_merged'] = data['y_train_merged']
        data['y_true_merged'] = data['y_true_merged']
        data['y_hat_x_true_merged'] = data['y_hat_x_true_merged']
        data['y_hat_merged'] = tb.merge(y_hat_log, dr.n_recurrent_step, dr.shift_data)

        # calculate loss
        loss_x = np.sum((data['x_hat_merged'].data.flatten() - data['x_true_merged'].flatten()) ** 2)
        loss_y = np.sum((data['y_hat_merged'].flatten() - data['y_true_merged'].flatten()) ** 2)
        loss_smooth = np.sum((data['x_hat_merged'].data[0:-1].flatten() - data['x_hat_merged'].data[1:].flatten()) ** 2)

        data['loss_x'].append(loss_x / data['loss_x_normalizer'])
        data['loss_y'].append(loss_y / data['loss_y_normalizer'])
        data['loss_smooth'].append(loss_smooth / data['loss_smooth_normalizer'])
        data['loss_total'].append((loss_y + dr.loss_weighting['smooth'] * loss_smooth) / (
            data['loss_y_normalizer'] + dr.loss_weighting['smooth'] * data['loss_smooth_normalizer']))
        return data_package

    def add_image_log(self, data_package):

        dp = data_package
        data = dp.data
        count_total = dp.data['total_iteration_count']
        image_log_dir = dp.IMAGE_LOG_DIR

        if not os.path.exists(image_log_dir):
            os.makedirs(image_log_dir)
        log_file_name_prefix = self.get_log_prefix(dp)

        plt.figure(figsize=(10, 10))
        plt.subplot(2, 2, 1)
        plt.plot(data['x_true_merged'], label='x_true')
        plt.plot(data['x_hat_merged'].get(), '--', label='x_hat')
        plt.xlabel('time')
        plt.ylabel('signal')
        plt.title('Iter = ' + str(count_total))
        plt.legend()

        if dp.IF_NOISED_Y:
            plt.subplot(2, 2, 2)
            plt.plot(data['y_train_merged'], label='y_train', alpha=0.5)
            plt.plot(data['y_true_merged'], label='y_true')
            plt.plot(data['y_hat_merged'], '--', label='y_hat')
            plt.xlabel('time')
            plt.ylabel('signal')
            plt.title('Iter = ' + str(count_total))
            plt.legend()
        else:
            plt.subplot(2, 2, 2)
            plt.plot(data['y_true_merged'], label='y_true')
            plt.plot(data['y_hat_merged'], '--', label='y_hat')
            plt.xlabel('time')
            plt.ylabel('signal')
            plt.title('Iter = ' + str(count_total))
            plt.legend()

        plt.subplot(2, 2, 3)
        plt.plot(data['y_true_merged'], label='by net_true')
        plt.plot(data['y_hat_x_true_merged'], '--', label='by net_hat')
        plt.xlabel('time')
        plt.ylabel('signal')
        plt.title('y reproduced with x_true, iter = ' + str(count_total))
        plt.legend()

        plt.subplot(2, 2, 4)
        plt.plot(data['loss_x'], '--', label='x loss')
        plt.plot(data['loss_y'], '-.', label='y loss')
        # plt.plot(spm_data['loss_smooth'], label='smooth loss')
        plt.plot(data['loss_total'], label='total loss')
        plt.xlabel('check point index')
        plt.ylabel('value')
        plt.title('Normalized loss, iter = ' + str(count_total))
        plt.legend()

        plt.tight_layout()
        plot_file_name = os.path.join(image_log_dir, log_file_name_prefix + '.png')
        plt.savefig(plot_file_name)
        plt.close()

    def add_data_log(self, data_package):

        dp = data_package
        data_log_dir = dp.DATA_LOG_DIR
        log_file_name_prefix = self.get_log_prefix(dp)
        if not os.path.exists(data_log_dir):
            os.makedirs(data_log_dir)
        file_name = os.path.join(data_log_dir, log_file_name_prefix + '.pkl')
        pickle.dump(data_package, open(file_name, "wb"))

    def train(self, dr, data_package):
        """"""
        print('Training starts!')
        dp = data_package
        data = dp.data
        data['loss_x'] = []
        data['loss_y'] = []
        data['loss_smooth'] = []
        data['loss_total'] = []
        x_hat_previous = data['x_hat_merged'].data.copy()  # for stop criterion checking
        data['total_iteration_count'] = 0

        isess = tf.Session()  # used for calculate log spm_data
        self.calculate_log_data(dr, data_package, isess)
        self.add_image_log(data_package)
        self.add_data_log(data_package)

        with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())
            for epoch in range(dp.MAX_EPOCHS):
                h_initial_segment = data['H_STATE_INITIAL']
                sess.run(tf.assign(dr.x_state_stacked_previous, data['x_hat_merged'].get(0)))
                for i_segment in range(dp.N_SEGMENTS):

                    for epoch_inner in range(dp.MAX_EPOCHS_INNER):
                        # assign proper spm_data
                        if dp.IF_NODE_MODE is True:
                            sess.run([tf.assign(dr.x_state_stacked,
                                                data['x_hat_merged'].get(i_segment).reshape(dr.n_recurrent_step, 1)),
                                      tf.assign(dr.h_state_initial, h_initial_segment)])
                        else:
                            sess.run([tf.assign(dr.x_state_stacked, data['x_hat_merged'].get(i_segment)),
                                      tf.assign(dr.h_state_initial, h_initial_segment)])

                        # training
                        sess.run(dr.train, feed_dict={dr.y_true: data['y_train'][i_segment]})

                        # collect results
                        data['x_hat_merged'].set(i_segment, sess.run(dr.x_state_stacked))

                        # add counting
                        data['total_iteration_count'] += 1

                        # Display logs per CHECK_STEPS step
                        if data['total_iteration_count'] % dp.CHECK_STEPS == 0:
                            self.calculate_log_data(dr, data_package, isess)
                            self.add_image_log(data_package)
                            self.add_data_log(data_package)

                            # saved summary = sess.run(dr.merged_summary)
                            # saved dr.summary_writer.add_summary(summary, count_total)

                            '''
                            print("Total iteration:", '%04d' % count_total, "loss_y=",
                                  "{:.9f}".format(spm_data['loss_y'][-1]))
                            print("Total iteration:", '%04d' % count_total, "loss_x=",
                                  "{:.9f}".format(spm_data['loss_x'][-1]))

                            if IF_IMAGE_LOG:
                                add_image_log(extra_prefix=LOG_EXTRA_PREFIX)

                            if IF_DATA_LOG:
                                add_data_log(extra_prefix=LOG_EXTRA_PREFIX)
                            '''
                            '''
                            # check stop criterion
                            relative_change = tb.rmse(x_hat_previous, spm_data['x_hat_merged'].get())
                            if relative_change < dr.stop_threshold:
                                print('Relative change: ' + str(relative_change))
                                print('Stop criterion met, stop training')
                            else:
                                # x_hat_previous = copy.deepcopy(spm_data['x_hat_merged'])
                                x_hat_previous = spm_data['x_hat_merged'].get().copy()
                            '''

                    # prepare for next segment
                    # update hemodynamic state initial
                    h_initial_segment = sess.run(dr.h_connector)
                    # update previous neural state
                    sess.run(tf.assign(dr.x_state_stacked_previous, data['x_hat_merged'].get(i_segment)))

        isess.close()

        print("Optimization Finished!")

        # spm_data['y_hat_log'] = y_hat_log

        return data_package

    def build_initializer_graph_and_train(self, dr, data_package):

        dr.build_an_initializer_graph(data_package.data['H_PARA_INITIAL'])

        self.train(dr, data_package)

        return data_package

#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""Test hierarchical CTC models (chainer)."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
import time
import unittest

sys.path.append('../../../../')
from models.chainer.ctc.hierarchical_ctc import HierarchicalCTC
from models.test.data import generate_data, idx2char, idx2word
from utils.measure_time_func import measure_time
from utils.evaluation.edit_distance import compute_cer, compute_wer
# from utils.training.learning_rate_controller import Controller


class TestCTC(unittest.TestCase):

    def test(self):
        print("Hierarchical CTC Working check.")

        # Pyramidal encoder
        self.check(encoder_type='lstm', bidirectional=True, subsample='drop')
        self.check(encoder_type='lstm', bidirectional=True, subsample='concat')

        # projection layer
        self.check(encoder_type='lstm', bidirectional=False, projection=True)

        # Label smoothing
        # self.check(encoder_type='lstm', bidirectional=True,
        #            label_smoothing=True)

        # Residual LSTM-CTC
        self.check(encoder_type='lstm', bidirectional=True,
                   residual=True)
        self.check(encoder_type='lstm', bidirectional=True,
                   dense_residual=True)

        # CLDNN-CTC
        self.check(encoder_type='lstm', bidirectional=True,
                   conv=True)
        self.check(encoder_type='lstm', bidirectional=True,
                   conv=True, batch_norm=True)

        self.check(encoder_type='lstm', bidirectional=True)

    @measure_time
    def check(self, encoder_type, bidirectional=False,
              subsample=False, projection=False,
              conv=False, batch_norm=False,
              residual=False, dense_residual=False, label_smoothing=False):

        print('==================================================')
        print('  encoder_type: %s' % encoder_type)
        print('  bidirectional: %s' % str(bidirectional))
        print('  projection: %s' % str(projection))
        print('  subsample: %s' % str(subsample))
        print('  conv: %s' % str(conv))
        print('  batch_norm: %s' % str(batch_norm))
        print('  residual: %s' % str(residual))
        print('  dense_residual: %s' % str(dense_residual))
        print('  label_smoothing: %s' % str(label_smoothing))
        print('==================================================')

        if conv:
            conv_channels = [32, 32]
            conv_kernel_sizes = [[41, 11], [21, 11]]
            conv_strides = [[2, 2], [2, 1]]
            poolings = [[], []]
            fc_list = [786, 786]
        else:
            conv_channels = []
            conv_kernel_sizes = []
            conv_strides = []
            poolings = []
            fc_list = []

        # Load batch data
        num_stack = 1 if subsample or conv else 2
        splice = 1
        xs, ys, ys_sub, x_lens, y_lens, y_lens_sub = generate_data(
            model_type='ctc',
            label_type='word_char',
            batch_size=2,
            num_stack=num_stack,
            splice=splice,
            backend='chainer')

        num_classes = 11
        num_classes_sub = 27

        # Load model
        model = HierarchicalCTC(
            input_size=xs[0].shape[-1] // splice // num_stack,   # 120
            encoder_type=encoder_type,
            bidirectional=bidirectional,
            num_units=256,
            num_proj=256 if projection else 0,
            num_layers=3,
            num_layers_sub=2,
            fc_list=fc_list,
            dropout=0.1,
            main_loss_weight=0.5,
            num_classes=num_classes,
            num_classes_sub=num_classes_sub,
            parameter_init=0.1,
            subsample_list=[] if not subsample else [True] * 3,
            num_stack=num_stack,
            splice=splice,
            conv_channels=conv_channels,
            conv_kernel_sizes=conv_kernel_sizes,
            conv_strides=conv_strides,
            poolings=poolings,
            batch_norm=batch_norm,
            label_smoothing_prob=0.1 if label_smoothing else 0,
            weight_noise_std=0,
            residual=residual,
            dense_residual=dense_residual)

        # Count total parameters
        for name in sorted(list(model.num_params_dict.keys())):
            num_params = model.num_params_dict[name]
            print("%s %d" % (name, num_params))
        print("Total %.3f M parameters" % (model.total_parameters / 1000000))

        # Define optimizer
        learning_rate = 1e-3
        model.set_optimizer('adam',
                            learning_rate_init=learning_rate,
                            weight_decay=1e-6,
                            clip_grad_norm=5,
                            lr_schedule=None,
                            factor=None,
                            patience_epoch=None)

        # Define learning rate controller
        # lr_controller = Controller(learning_rate_init=learning_rate,
        #                            backend='chainer',
        #                            decay_start_epoch=20,
        #                            decay_rate=0.9,
        #                            decay_patient_epoch=10,
        #                            lower_better=True)

        # GPU setting
        model.set_cuda(deterministic=False, benchmark=True)

        # Train model
        max_step = 1000
        start_time_step = time.time()
        for step in range(max_step):

            # Step for parameter update
            model.optimizer.target.cleargrads()
            loss, loss_main, loss_sub = model(
                xs, ys, ys_sub,
                x_lens, y_lens, y_lens_sub)
            loss.backward()
            loss.unchain_backward()
            model.optimizer.update()

            # Inject Gaussian noise to all parameters

            if (step + 1) % 10 == 0:
                # Decode
                labels_pred = model.decode(xs, x_lens, beam_width=1)
                labels_pred_sub = model.decode(
                    xs, x_lens, beam_width=1, is_sub_task=True)

                # Compute accuracy
                str_true = idx2word(ys[0, :y_lens[0]])
                str_pred = idx2word(labels_pred[0])
                ler = compute_wer(ref=str_true.split('_'),
                                  hyp=str_pred.split('_'),
                                  normalize=True)
                str_true_sub = idx2char(ys_sub[0, :y_lens_sub[0]])
                str_pred_sub = idx2char(labels_pred_sub[0])
                ler_sub = compute_cer(ref=str_true_sub.replace('_', ''),
                                      hyp=str_pred_sub.replace('_', ''),
                                      normalize=True)

                duration_step = time.time() - start_time_step
                print('Step %d: loss = %.3f (%.3f/%.3f) / ler = %.3f (%.3f) / lr = %.5f (%.3f sec)' %
                      (step + 1, loss.data, loss_main.data, loss_sub.data,
                       ler, ler_sub, learning_rate, duration_step))
                start_time_step = time.time()

                # Visualize
                print('Ref: %s' % str_true)
                print('Hyp (word): %s' % str_pred)
                print('Hyp (char): %s' % str_pred_sub)

                if ler_sub < 0.1:
                    print('Modle is Converged.')
                    break

                # Update learning rate
                # model.optimizer, learning_rate = lr_controller.decay_lr(
                #     optimizer=model.optimizer,
                #     learning_rate=learning_rate,
                #     epoch=step,
                #     value=ler)


if __name__ == "__main__":
    unittest.main()

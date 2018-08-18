
import numpy as np

import scipy.ndimage as spnd

import sys

import image_utils

sys.path.append('../cnn_utils')

from augmentation_functions import augSaturation,augBlur,augNoise,augScale,augRotate,randScale,randRot, augProjective, randFlip, augFlip,  augShift, rand_colorspace, rand_channels, augCrop
import sampling_utils

def make_affine_matrix_batch(
		batch_size,
		thetas=None, scales=None, trans_x=None, trans_y=None,
		do_flip_horiz=None, do_flip_vert=None
):

	# hacky way to set default vals
	params =  [thetas, scales, trans_x, trans_y, do_flip_horiz, do_flip_vert]
	for pi, param in enumerate(params):
		if param is None:
			params[pi] = np.zeros((batch_size,))
	thetas, scales, trans_x, trans_y, do_flip_horiz, do_flip_vert = params

	T = np.zeros((batch_size, 2, 3))

	flip_horiz_factor = -1 * do_flip_horiz.astype(float)
	flip_vert_factor = -1 * do_flip_vert.astype(float)
	# rotation and scaling
	T[:, 0, 0] = np.cos(thetas) * scales * flip_horiz_factor
	T[:, 0, 1] = -np.sin(thetas)
	T[:, 1, 0] = np.sin(thetas)
	T[:, 1, 1] = np.cos(thetas) * scales * flip_vert_factor

	# translation
	T[:, 0, 2] = trans_x
	T[:, 1, 2] = trans_y
	return T

def aug_params_to_transform_matrices(
		batch_size,
		max_rot=0.,
		scale_range=(0, 0),
		max_trans=(0, 0),  # x, y
		apply_flip=False,
	):
	if not isinstance(scale_range, tuple) and not isinstance(scale_range,list):
		scale_range = (1 - scale_range, 1 + scale_range)

	if not isinstance(max_trans, tuple) and not isinstance(max_trans, list):
		max_trans = (max_trans, max_trans)


	thetas = np.pi * (np.random.rand(batch_size) * max_rot * 2. - max_rot) / 180.
	scales = np.random.rand(batch_size) * (scale_range[1] - scale_range[0]) + scale_range[0]
	trans_x = np.random.rand(batch_size) * max_trans[0] * 2. - max_trans[0]
	trans_y = np.random.rand(batch_size) * max_trans[1] * 2. - max_trans[1]

	if apply_flip:
		do_flip_horiz = np.random.rand(batch_size) > 0.5
		do_flip_vert = np.random.rand(batch_size) > 0.5
	else:
		do_flip_horiz = np.zeros((batch_size,))
		do_flip_vert = np.zeros((batch_size,))

	T = make_affine_matrix_batch(
		thetas, scales, trans_x, trans_y,
		do_flip_horiz, do_flip_vert
	)

	return T

def _apply_flow_batch(X_batch, flow_batch):
	xv, yv = np.meshgrid(np.linspace(0, X_batch.shape[2], X_batch.shape[2], endpoint=False),
						 np.linspace(0, X_batch.shape[1], X_batch.shape[1], endpoint=False))

	X_aug = X_batch.copy()
	for bi in range(X_batch.shape[0]):
		map_coords = np.reshape(
			np.concatenate([xv[:, :, np.newaxis] + np.expand_dims(flow_batch[bi, :, :, 0], axis=-1),
							yv[:, :, np.newaxis] + np.expand_dims(flow_batch[bi, :, :, 1], axis=-1)],
						   axis=-1),
			(X_batch.shape[1] * X_batch.shape[2], 2))

		for c in range(X_batch.shape[-1]):
			X_aug[bi, :, :, c] = np.reshape(
				spnd.map_coordinates(X_batch[bi, :, :, c].transpose(), np.transpose(map_coords, (1, 0)), cval=0.),
				X_batch.shape[1:3])
	return X_aug


def aug_rand_flow(X, flow_sigma, blur_sigma):
	flow_batch = sampling_utils.sample_flow_batch(X.shape, flow_sigma, blur_sigma)
	X_aug = _apply_flow_batch(X, flow_batch)
	return X_aug, flow_batch


def aug_mtg_batch( 
		X, 
		crop_to_size_range=None,
		pad_to_size=None,
		max_sat = 0., 
		max_rot = 0., 
		scale_range=(0, 0), max_noise_std=0., scale_range_horiz=(0,0), 
		apply_blur = False, max_proj = 0., apply_flip = False, max_trans = 0., 
		masks=None, 
		border_val=(1., 1., 1.), rot_range = None):

	batch_size = X.shape[0]
	aug_params = {
		'scales': None,
		'rotations': None }
	aug_params = dict()							
	if not isinstance(scale_range, tuple) and not isinstance(scale_range,list):
		scale_range = (1 - scale_range, 1 + scale_range)

	if max_rot > 0 or rot_range is not None:
		aug_params['rotations'] = [None]*batch_size

	if isinstance(max_proj, list) or max_proj > 0:
		aug_params['proj_theta'] = [None]*batch_size

	if scale_range[0] > 0 and scale_range[1]>0:
 		aug_params['scales'] = [None]*batch_size	
	if apply_flip:
 		aug_params['flip_x'] = [None]*batch_size	
 		aug_params['flip_y'] = [None]*batch_size	
	if max_trans > 0:
 		aug_params['trans_x'] = [None]*batch_size	
 		aug_params['trans_y'] = [None]*batch_size

	if crop_to_size_range is not None:
		aug_params['crop_center'] = [None] * batch_size	
		aug_params['crop_to_size'] = [None] * batch_size

	if np.min(X) < 0:
		#print('Input to augmentation is normalized, unnormalizing...')
		normalized = True
		X_aug = image_utils.inverse_normalize(X.copy())
	else:
		normalized = False
		X_aug = X.copy()

	if pad_to_size is None:
		X_out = X_aug
	else:
		X_out = np.zeros((X_aug.shape[0], ) + pad_to_size + X_aug.shape[3:])

	if X.shape[-1] >= 3:
		max_n_chans = 3
	else:
		max_n_chans = 1

	for bi in range(X.shape[0]):
		for cgi in range(X.shape[-1]/max_n_chans):
			curr_X = X_aug[bi, :, :, cgi*max_n_chans : (cgi + 1)*max_n_chans]

			# if grayscale, make border value a single value
			if curr_X.shape[-1] == 1:
				curr_X = curr_X[:,:,0]
				bv = border_val[0]
			else:
				bv = border_val

			if crop_to_size_range is not None:
				curr_X, crop_to_size, crop_center = augCrop(curr_X, 
					pad_to_size=pad_to_size, crop_to_size_range=crop_to_size_range, border_color=bv)
				aug_params['crop_to_size'][bi] = crop_to_size
				aug_params['crop_center'][bi] = crop_center

			if masks is not None:
				if max_noise_std > 0:
					curr_X = augNoise(curr_X,max_noise_std)

				if apply_blur:	
					curr_X = augBlur(curr_X)
				curr_X = curr_X * masks[bi]
				curr_X += 1-masks[bi]
		
			if rot_range:
				if cgi == 0:
					rot_deg = np.random.rand(1) * (rot_range[1]-rot_range[0]) + rot_range[0]
				curr_X,_, rotation_theta = augRotate(curr_X, None, 
					degree_rand=rot_deg, border_color=bv)
				assert rotation_theta == rot_deg
			
				aug_params['rotations'][bi] = rotation_theta
			elif max_rot > 0:
				if cgi == 0:
					rot_deg = np.random.rand(1) * 2 * max_rot - max_rot
				curr_X,_, rotation_theta = augRotate(curr_X, None, 
					degree_rand=rot_deg, border_color=bv)
				aug_params['rotations'][bi] = rotation_theta
			if max_proj > 0 or type(max_proj) == list:
				curr_X, projection_theta = augProjective( curr_X, scale = 1., max_theta = max_proj, max_shear=None)
				aug_params['proj_theta'][bi] = projection_theta
	#			curr_X,_ = augScale(curr_X, None, scale_rand = scale, border_color=(1.,1.,1.) )

			if scale_range[0] > 0. and scale_range[1] > 0:
				if cgi == 0:
					scale = randScale(scale_range[0], scale_range[1])
	#			print('augmenting random scale = {}'.format(scale))
				aug_params['scales'][bi] = scale
				curr_X,_ = augScale(curr_X, None, scale_rand = scale, 
					border_color=bv)
			if scale_range_horiz[0] > 0.:
				scale_horiz = randScale(scale_range_horiz[0], scale_range_horiz[1])
				curr_X,_ = augScale(curr_X, None, scale_rand = (scale_horiz, 1.), 
					border_color=bv)
				
			if masks is None:
				if max_noise_std > 0:
					curr_X = augNoise(curr_X,max_noise_std)

				if apply_blur:	
					curr_X = augBlur(curr_X)

			if apply_flip:
				if cgi == 0:
					do_flip_x = randFlip()
				aug_params['flip_x'][bi] = do_flip_x
				curr_X,_,_ = augFlip( curr_X, flip_rand = do_flip_x )

				if cgi == 0:
					do_flip_y = randFlip()
				aug_params['flip_y'][bi] = do_flip_y
				if do_flip_y:
					curr_X  = np.flipud(curr_X)

			if max_trans > 0:
				if cgi == 0:
					trans_x = int(np.random.rand(1) * 2 * max_trans - max_trans)
				aug_params['trans_x'][bi] = trans_x
				#curr_X = np.roll( curr_X, trans_x, axis=1)
				if cgi == 0:
					trans_y = int(np.random.rand(1) * 2*max_trans - max_trans)
				aug_params['trans_y'][bi] = trans_y
				curr_X, _ = augShift(curr_X, rand_shift=(trans_x, trans_y), border_color=bv)

			if max_sat > 0:
				if cgi == 0:
					rand_cs = rand_colorspace()
					rand_c = rand_channels(rand_cs)
					rand_sat = np.random.rand(1) * 2 * max_sat - max_sat + 1.0
				curr_X = augSaturation( curr_X, aug_percent=rand_sat, aug_colorspace=rand_cs,aug_channels=rand_c)

			if len(curr_X.shape) < 3:
				curr_X = np.expand_dims(curr_X, axis=-1)

			X_out[bi, :, :, cgi*max_n_chans:(cgi + 1)*max_n_chans] = np.reshape(
				curr_X, X_out[bi, :, :, cgi*max_n_chans:(cgi + 1)*max_n_chans].shape)


	X_out = np.clip(X_out, 0., 1.)

	if normalized:
		X_out = image_utils.normalize(X_out)
	return X_out, aug_params

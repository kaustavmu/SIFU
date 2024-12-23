import torch
import typing
import numpy as np

__all__ = ["VPoser1"]

def _rotation_matrix_to_quaternion(rotation_matrix, eps=1e-6):
    """Convert 3x4 rotation matrix to 4d quaternion vector

    This algorithm is based on algorithm described in
    https://github.com/KieranWynn/pyquaternion/blob/master/pyquaternion/quaternion.py#L201

    Args:
        rotation_matrix (Tensor): the rotation matrix to convert.

    Return:
        Tensor: the rotation in quaternion

    Shape:
        - Input: :math:`(N, 3, 4)`
        - Output: :math:`(N, 4)`

    Example:
        >>> input = torch.rand(4, 3, 4)  # Nx3x4
        >>> output = tgm.rotation_matrix_to_quaternion(input)  # Nx4
    """
    if not torch.is_tensor(rotation_matrix):
        raise TypeError("Input type is not a torch.Tensor. Got {}".format(
            type(rotation_matrix)))

    if len(rotation_matrix.shape) > 3:
        raise ValueError(
            "Input size must be a three dimensional tensor. Got {}".format(
                rotation_matrix.shape))
    if not rotation_matrix.shape[-2:] == (3, 4):
        raise ValueError(
            "Input size must be a N x 3 x 4  tensor. Got {}".format(
                rotation_matrix.shape))

    rmat_t = torch.transpose(rotation_matrix, 1, 2)

    mask_d2 = rmat_t[:, 2, 2] < eps

    mask_d0_d1 = rmat_t[:, 0, 0] > rmat_t[:, 1, 1]
    mask_d0_nd1 = rmat_t[:, 0, 0] < -rmat_t[:, 1, 1]

    t0 = 1 + rmat_t[:, 0, 0] - rmat_t[:, 1, 1] - rmat_t[:, 2, 2]
    q0 = torch.stack([rmat_t[:, 1, 2] - rmat_t[:, 2, 1],
                      t0, rmat_t[:, 0, 1] + rmat_t[:, 1, 0],
                      rmat_t[:, 2, 0] + rmat_t[:, 0, 2]], -1)
    t0_rep = t0.repeat(4, 1).t()

    t1 = 1 - rmat_t[:, 0, 0] + rmat_t[:, 1, 1] - rmat_t[:, 2, 2]
    q1 = torch.stack([rmat_t[:, 2, 0] - rmat_t[:, 0, 2],
                      rmat_t[:, 0, 1] + rmat_t[:, 1, 0],
                      t1, rmat_t[:, 1, 2] + rmat_t[:, 2, 1]], -1)
    t1_rep = t1.repeat(4, 1).t()

    t2 = 1 - rmat_t[:, 0, 0] - rmat_t[:, 1, 1] + rmat_t[:, 2, 2]
    q2 = torch.stack([rmat_t[:, 0, 1] - rmat_t[:, 1, 0],
                      rmat_t[:, 2, 0] + rmat_t[:, 0, 2],
                      rmat_t[:, 1, 2] + rmat_t[:, 2, 1], t2], -1)
    t2_rep = t2.repeat(4, 1).t()

    t3 = 1 + rmat_t[:, 0, 0] + rmat_t[:, 1, 1] + rmat_t[:, 2, 2]
    q3 = torch.stack([t3, rmat_t[:, 1, 2] - rmat_t[:, 2, 1],
                      rmat_t[:, 2, 0] - rmat_t[:, 0, 2],
                      rmat_t[:, 0, 1] - rmat_t[:, 1, 0]], -1)
    t3_rep = t3.repeat(4, 1).t()

    mask_c0 = mask_d2 * mask_d0_d1
    mask_c1 = mask_d2 * torch.logical_not(mask_d0_d1)
    mask_c2 = torch.logical_not(mask_d2) * mask_d0_nd1
    mask_c3 = torch.logical_not(mask_d2) * torch.logical_not(mask_d0_nd1)
    mask_c0 = mask_c0.view(-1, 1).type_as(q0)
    mask_c1 = mask_c1.view(-1, 1).type_as(q1)
    mask_c2 = mask_c2.view(-1, 1).type_as(q2)
    mask_c3 = mask_c3.view(-1, 1).type_as(q3)

    q = q0 * mask_c0 + q1 * mask_c1 + q2 * mask_c2 + q3 * mask_c3
    q /= torch.sqrt(t0_rep * mask_c0 + t1_rep * mask_c1 +  # noqa
                    t2_rep * mask_c2 + t3_rep * mask_c3)  # noqa
    q *= 0.5
    return q

def _quaternion_to_angle_axis(quaternion) -> torch.Tensor:
    """Convert quaternion vector to angle axis of rotation.

    Adapted from ceres C++ library: ceres-solver/include/ceres/rotation.h

    Args:
        quaternion (torch.Tensor): tensor with quaternions.

    Return:
        torch.Tensor: tensor with angle axis of rotation.

    Shape:
        - Input: :math:`(*, 4)` where `*` means, any number of dimensions
        - Output: :math:`(*, 3)`

    Example:
        >>> quaternion = torch.rand(2, 4)  # Nx4
        >>> angle_axis = tgm.quaternion_to_angle_axis(quaternion)  # Nx3
    """
    if not torch.is_tensor(quaternion):
        raise TypeError("Input type is not a torch.Tensor. Got {}".format(
            type(quaternion)))

    if not quaternion.shape[-1] == 4:
        raise ValueError("Input must be a tensor of shape Nx4 or 4. Got {}"
                         .format(quaternion.shape))
    # unpack input and compute conversion
    q1 = quaternion[..., 1]
    q2 = quaternion[..., 2]
    q3 = quaternion[..., 3]
    sin_squared_theta = q1 * q1 + q2 * q2 + q3 * q3

    sin_theta = torch.sqrt(sin_squared_theta)
    cos_theta = quaternion[..., 0]
    two_theta = 2.0 * torch.where(
        cos_theta < 0.0,
        torch.atan2(-sin_theta, -cos_theta),
        torch.atan2(sin_theta, cos_theta))

    k_pos = two_theta / sin_theta
    k_neg = 2.0 * torch.ones_like(sin_theta)
    k = torch.where(sin_squared_theta > 0.0, k_pos, k_neg)

    angle_axis = torch.zeros_like(quaternion)[..., :3]
    angle_axis[..., 0] += q1 * k
    angle_axis[..., 1] += q2 * k
    angle_axis[..., 2] += q3 * k
    return angle_axis

def _rotation_matrix_to_angle_axis(rotation_matrix):
    """Convert 3x4 rotation matrix to Rodrigues vector

    Args:
        rotation_matrix (Tensor): rotation matrix.

    Returns:
        Tensor: Rodrigues vector transformation.

    Shape:
        - Input: :math:`(N, 3, 4)`
        - Output: :math:`(N, 3)`

    Example:
        >>> input = torch.rand(2, 3, 4)  # Nx4x4
        >>> output = tgm.rotation_matrix_to_angle_axis(input)  # Nx3
    """
    # todo add check that matrix is a valid rotation matrix
    quaternion = _rotation_matrix_to_quaternion(rotation_matrix)
    return _quaternion_to_angle_axis(quaternion)

def _matrot2aa(pose_matrot):
    '''
    :param pose_matrot: Nx3x3
    :return: Nx3
    '''
    bs = pose_matrot.size(0)
    homogen_matrot = torch.nn.functional.pad(pose_matrot, [0,1])
    pose = _rotation_matrix_to_angle_axis(homogen_matrot)
    return pose

class ContinousRotReprDecoder(torch.nn.Module):
    def __init__(self):
        super(ContinousRotReprDecoder, self).__init__()

    def forward(self, module_input):
        reshaped_input = module_input.view(-1, 3, 2)
        b1 = torch.nn.functional.normalize(reshaped_input[:, :, 0], dim=1)
        dot_prod = torch.sum(b1 * reshaped_input[:, :, 1], dim=1, keepdim=True)
        b2 = torch.nn.functional.normalize(
            reshaped_input[:, :, 1] - dot_prod * b1,
            dim=-1
        )
        b3 = torch.cross(b1, b2, dim=1)
        return torch.stack([b1, b2, b3], dim=-1)

class NormalDistDecoder(torch.nn.Module):
    def __init__(self, num_feat_in, latentD):
        super(NormalDistDecoder, self).__init__()
        self.mu = torch.nn.Linear(num_feat_in, latentD)
        self.logvar = torch.nn.Linear(num_feat_in, latentD)

    def forward(self, Xout):
        return torch.distributions.normal.Normal(
            self.mu(Xout), 
            torch.nn.functional.softplus(self.logvar(Xout))
        )
    
#NOTE: code from https://github.com/nghorbani/human_body_prior
class VPoser_v1(torch.nn.Module):
    def __init__(self, num_neurons, latentD, data_shape, use_cont_repr=True):
        super(VPoser_v1, self).__init__()

        self.latentD = latentD
        self.use_cont_repr = use_cont_repr

        n_features = np.prod(data_shape)
        self.num_joints = data_shape[1]

        self.bodyprior_enc_bn1 = torch.nn.BatchNorm1d(n_features)
        self.bodyprior_enc_fc1 = torch.nn.Linear(n_features, num_neurons)
        self.bodyprior_enc_bn2 = torch.nn.BatchNorm1d(num_neurons)
        self.bodyprior_enc_fc2 = torch.nn.Linear(num_neurons, num_neurons)
        self.bodyprior_enc_mu = torch.nn.Linear(num_neurons, latentD)
        self.bodyprior_enc_logvar = torch.nn.Linear(num_neurons, latentD)
        self.dropout = torch.nn.Dropout(p=.1, inplace=False)

        self.bodyprior_dec_fc1 = torch.nn.Linear(latentD, num_neurons)
        self.bodyprior_dec_fc2 = torch.nn.Linear(num_neurons, num_neurons)

        if self.use_cont_repr:
            self.rot_decoder = ContinousRotReprDecoder()

        self.bodyprior_dec_out = torch.nn.Linear(num_neurons, self.num_joints* 6)

    def encode(self, Pin):
        '''

        :param Pin: Nx(numjoints*3)
        :param rep_type: 'matrot'/'aa' for matrix rotations or axis-angle
        :return:
        '''
        Xout = Pin.view(Pin.size(0), -1)  # flatten input
        Xout = self.bodyprior_enc_bn1(Xout)

        Xout = torch.nn.functional.leaky_relu(self.bodyprior_enc_fc1(Xout), negative_slope=.2)
        Xout = self.bodyprior_enc_bn2(Xout)
        Xout = self.dropout(Xout)
        Xout = torch.nn.functional.leaky_relu(self.bodyprior_enc_fc2(Xout), negative_slope=.2)
        return torch.distributions.normal.Normal(
            self.bodyprior_enc_mu(Xout), 
            torch.nn.functional.softplus(self.bodyprior_enc_logvar(Xout))
        )

    def decode(self, Zin, output_type='matrot'):
        assert output_type in ['matrot', 'aa']

        Xout = torch.nn.functional.leaky_relu(self.bodyprior_dec_fc1(Zin), negative_slope=.2)
        Xout = self.dropout(Xout)
        Xout = torch.nn.functional.leaky_relu(self.bodyprior_dec_fc2(Xout), negative_slope=.2)
        Xout = self.bodyprior_dec_out(Xout)
        if self.use_cont_repr:
            Xout = self.rot_decoder(Xout)
        else:
            Xout = torch.tanh(Xout)

        Xout = Xout.view([-1, 1, self.num_joints, 9])
        bs = Zin.shape[0]
        if output_type == 'aa': return _matrot2aa(Xout.view(-1, 3, 3)).view(bs, -1, 3)
        return Xout

    def forward(self, Pin, input_type='matrot', output_type='matrot'):
        '''

        :param Pin: aa: Nx1xnum_jointsx3 / matrot: Nx1xnum_jointsx9
        :param input_type: matrot / aa for matrix rotations or axis angles
        :param output_type: matrot / aa
        :return:
        '''
        assert output_type in ['matrot', 'aa']
        # if input_type == 'aa': Pin = VPoser.aa2matrot(Pin)
        # if Pin.size(3) == 3: Pin = VPoser.aa2matrot(Pin)
        q_z = self.encode(Pin)
        q_z_sample = q_z.rsample()
        Prec = self.decode(q_z_sample)

        results = {'mean':q_z.mean, 'std':q_z.scale}
        bs = Pin.shape[0]
        if output_type == 'aa': results['pose_aa'] = _matrot2aa(Prec.view(-1, 3, 3)).view(bs, -1, 3)
        else: results['pose_matrot'] = Prec
        return results

    def sample_poses(self, num_poses, output_type='aa', seed=None):
        np.random.seed(seed)
        dtype = self.bodyprior_dec_fc1.weight.dtype
        device = self.bodyprior_dec_fc1.weight.device
        self.eval()
        with torch.no_grad():
            Zgen = torch.tensor(np.random.normal(0., 1., size=(num_poses, self.latentD)), dtype=dtype).to(device)
        return self.decode(Zgen, output_type=output_type)
    
class VPoser1(VPoser_v1):
    def __init__(self,
        flatten_pose:       bool=True,
    ):
        super().__init__(
            num_neurons=512,
            latentD=32,
            data_shape=[1, 21, 3],
            use_cont_repr=True,
        )
        self.eval()
        self.flatten_pose = flatten_pose
    
    def forward(self,
        encode:         torch.Tensor=None,
        decode:         torch.Tensor=None,
        autoencode:     torch.Tensor=None,
    ) -> typing.Mapping[str, torch.Tensor]:
        out = { }        
        if autoencode is not None:
            # decoded = super(VPoser2, self).forward(autoencode)
            q_z = self.encode(autoencode)
            # q_z_sample = q_z.rsample()
            q_z_sample = q_z.mean
            dec = self.decode(q_z_sample)
            bs = autoencode.shape[0]
            decoded = {
                'pose_body': _matrot2aa(dec.view(-1, 3, 3)).view(bs, -1, 3),
                'pose_body_matrot': dec.view(bs, -1, 9),
            }
            decoded.update({
                'poZ_body_mean': q_z.mean,
                'poZ_body_std': q_z.scale,
                'q_z': q_z,
            })
            out['pose'] = decoded['pose_body']
            out['embedding'] = decoded['poZ_body_mean'] # decoded['q_z']
            if self.flatten_pose:
                out['pose'] = out['pose'].reshape(autoencode.shape[0], -1)
            return out
        if encode is not None:
            out['embedding'] = self.encode(encode).mean
        if decode is not None:
            dec = self.decode(decode)
            bs = decode.shape[0]            
            out['pose'] = _matrot2aa(dec.view(-1, 3, 3)).view(bs, -1, 3)
            if self.flatten_pose:
                out['pose'] = out['pose'].reshape(decode.shape[0], -1)
        return out
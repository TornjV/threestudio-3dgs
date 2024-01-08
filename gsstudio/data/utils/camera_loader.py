import json
import os

import torch
from threestudio.renderer.renderer_utils import convert_gl2cv
from threestudio.utils.typing import *
from tqdm import tqdm

import gsstudio

from .camera_utils import CameraOutput, intrinsic2proj_mtx, matrix2rays
from .data_utils import batch_merge_output
from .image_utils import ImageOutput


class CameraLoader:
    def __init__(
        self,
        camera_dict,
        max_nums=-1,
        interval=1,
        scale=1,
        offline_load=False,
        normalize=True,
    ):
        camera_dict = json.load(
            open(os.path.join(self.cfg.dataroot, "transforms.json"), "r")
        )
        assert camera_dict["camera_model"] == "OPENCV"

        frames = camera_dict["frames"]
        camera_list = []
        image_list = []
        frames = frames[::interval]
        if max_nums > 0:
            frames = frames[:max_nums]

        for idx, frame in tqdm(enumerate(frames)):
            # load camera
            camera = CameraOutput()
            camera.width = frame["w"] // scale
            camera.height = frame["h"] // scale

            intrinsic: Float[Tensor, "4 4"] = torch.eye(4)
            intrinsic[0, 0] = frame["fl_x"] / scale
            intrinsic[1, 1] = frame["fl_y"] / scale
            intrinsic[0, 2] = frame["cx"] / scale
            intrinsic[1, 2] = frame["cy"] / scale

            camera.intrinsic = intrinsic.unsqueeze(0)
            extrinsic: Float[Tensor, "4 4"] = torch.as_tensor(
                frame["transform_matrix"], dtype=torch.float32
            )
            camera.c2w = extrinsic.unsqueeze(0)
            camera.c2w, camera.intrinsic = convert_gl2cv(camera.c2w, camera.intrinsic)

            (
                camera.fovx,
                camera.fovy,
                camera.cx,
                camera.cy,
                camera.proj_mtx,
            ) = intrinsic2proj_mtx(camera.intrinsic, camera.width, camera.height)
            if offline_load:
                camera.rays_o, camera.rays_d = matrix2rays(
                    camera.c2w,
                    camera.intrinsic,
                    camera.height,
                    camera.width,
                    normalize=normalize,
                )

            moment: Float[Tensor, "1"] = torch.zeros(1)
            if frame.__contains__("moment"):
                moment[0] = frame["moment"]
            else:
                moment[0] = 0
            camera.camera_time = moment

            # load image
            image = ImageOutput()
            frame_path = os.path.join(self.cfg.dataroot, frame["file_path"])
            image.frame_path = [frame_path]
            if frame.__contains__("mask_path"):
                mask_path = os.path.join(self.cfg.dataroot, frame["mask_path"])
                image.mask_path = [mask_path]
            if frame.__contains__("bbox"):
                image.bbox = torch.FloatTensor(frame["bbox"]).unsqueeze(0) / scale
            if offline_load:
                image.load_image()

            camera_list.append(camera)
            image_list.append(image)

        self.cameras = batch_merge_output(camera_list)
        self.images = batch_merge_output(image_list)

    def set_layout(self, camera_layout="default", camera_distance=-1):
        if camera_layout == "around":
            self.cameras.c2w[:, :3, 3] -= torch.mean(
                self.cameras.c2w[:, :3, 3], dim=0
            ).unsqueeze(0)
        elif camera_layout == "front":
            assert camera_distance > 0
            self.cameras.c2w[:, :3, 3] -= torch.mean(
                self.cameras.c2w[:, :3, 3], dim=0
            ).unsqueeze(0)
            z_vector = torch.zeros(self.cameras.c2w.shape[0], 3, 1)
            z_vector[:, 2, :] = 1
            rot_z_vector = self.cameras.c2w[:, :3, :3] @ z_vector
            rot_z_vector = torch.mean(rot_z_vector, dim=0).unsqueeze(0)
            self.cameras.c2w[:, :3, 3] -= rot_z_vector[:, :, 0] * camera_distance
        elif camera_layout == "default":
            pass
        else:
            raise ValueError(
                f"Unknown camera layout {self.cfg.camera_layout}. Now support only around and front."
            )

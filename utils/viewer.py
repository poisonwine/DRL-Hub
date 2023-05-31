import cv2
import numpy as np
import pdb
import os

class VideoWriter(object):

    def __init__(self, out_dir="./output/", fps=24, resolution=(800, 600), min_len=10):
        out_dir, out_name = "/".join(out_dir.split("/")[:-1]), out_dir.split("/")[-1]
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
        self.imgs = []
        fourcc = cv2.VideoWriter_fourcc('M', 'J', 'P', 'G')  # opencv3.0
        self.fps = fps
        self.videowriter = cv2.VideoWriter(os.path.join(out_dir, out_name), fourcc, fps, resolution)
        self.min_video_len = min_len

    def reset(self):
        self.imgs.clear()

    def add_frame(self, img):
        if isinstance(img, list):
            self.imgs += img
        else:
            self.imgs.append(img)

    def save(self):
        for img in self.imgs:
            frame = img[:, :, ::-1] # RGB to BGR
            self.videowriter.write(frame.astype(np.uint8))

        self.videowriter.release()
        self.reset()
        print('Finish!')
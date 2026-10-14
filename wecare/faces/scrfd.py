"""Detector de caras SCRFD (InsightFace) sobre onnxruntime.

Reimplementación compacta del pre/post-procesado de `insightface.model_zoo.scrfd`
(licencia MIT) para no depender del paquete `insightface`, que requiere compilar
extensiones nativas. Funciona con los modelos `det_500m.onnx` (buffalo_sc) y
`det_10g.onnx` (buffalo_l).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import onnxruntime as ort


@dataclass
class Face:
    bbox: np.ndarray  # [x1, y1, x2, y2] en píxeles de la imagen original
    score: float
    kps: np.ndarray  # (5, 2) ojos, nariz, comisuras

    @property
    def width(self) -> float:
        return float(self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        return float(self.bbox[3] - self.bbox[1])

    @property
    def center(self) -> tuple[float, float]:
        return (float(self.bbox[0] + self.bbox[2]) / 2, float(self.bbox[1] + self.bbox[3]) / 2)


def _distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _distance2kps(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    preds = []
    for i in range(0, distance.shape[1], 2):
        preds.append(points[:, i % 2] + distance[:, i])
        preds.append(points[:, i % 2 + 1] + distance[:, i + 1])
    return np.stack(preds, axis=-1)


def _nms(dets: np.ndarray, thresh: float) -> list[int]:
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


class SCRFD:
    """Detector de caras con puntos clave (5 landmarks)."""

    INPUT_MEAN = 127.5
    INPUT_STD = 128.0

    def __init__(self, model_path: str, det_size: tuple[int, int] = (640, 640),
                 det_thresh: float = 0.5, nms_thresh: float = 0.4):
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(model_path, sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        self.det_size = det_size
        self.det_thresh = det_thresh
        self.nms_thresh = nms_thresh
        self._center_cache: dict[tuple[int, int, int], np.ndarray] = {}

        n_out = len(self.output_names)
        if n_out in (6, 9):
            self.fmc, self.strides, self.num_anchors = 3, [8, 16, 32], 2
        elif n_out in (10, 15):
            self.fmc, self.strides, self.num_anchors = 5, [8, 16, 32, 64, 128], 1
        else:
            raise ValueError(f"Modelo SCRFD no reconocido: {n_out} salidas")
        self.use_kps = n_out in (9, 15)
        if not self.use_kps:
            raise ValueError("Se requiere un modelo SCRFD con landmarks (det_500m / det_10g)")

    def _forward(self, img: np.ndarray, threshold: float):
        h_in, w_in = img.shape[:2]
        blob = cv2.dnn.blobFromImage(img, 1.0 / self.INPUT_STD, (w_in, h_in),
                                     (self.INPUT_MEAN,) * 3, swapRB=True)
        outs = self.session.run(self.output_names, {self.input_name: blob})
        # Algunos exports devuelven (1, N, C); otros (N, C).
        batched = outs[0].ndim == 3
        scores_l, bboxes_l, kpss_l = [], [], []
        for idx, stride in enumerate(self.strides):
            scores = outs[idx][0] if batched else outs[idx]
            bbox_preds = (outs[idx + self.fmc][0] if batched else outs[idx + self.fmc]) * stride
            kps_preds = (outs[idx + self.fmc * 2][0] if batched else outs[idx + self.fmc * 2]) * stride
            height, width = h_in // stride, w_in // stride
            key = (height, width, stride)
            centers = self._center_cache.get(key)
            if centers is None:
                centers = np.stack(np.mgrid[:height, :width][::-1], axis=-1).astype(np.float32)
                centers = (centers * stride).reshape((-1, 2))
                if self.num_anchors > 1:
                    centers = np.stack([centers] * self.num_anchors, axis=1).reshape((-1, 2))
                self._center_cache[key] = centers
            scores = scores.reshape(-1)
            pos = np.where(scores >= threshold)[0]
            scores_l.append(scores[pos])
            bboxes_l.append(_distance2bbox(centers, bbox_preds)[pos])
            kpss = _distance2kps(centers, kps_preds).reshape((centers.shape[0], -1, 2))
            kpss_l.append(kpss[pos])
        return scores_l, bboxes_l, kpss_l

    def detect(self, img: np.ndarray, max_num: int = 0) -> list[Face]:
        """Detecta caras en una imagen BGR. Devuelve una lista ordenada por score."""
        in_w, in_h = self.det_size
        im_ratio = img.shape[0] / img.shape[1]
        model_ratio = in_h / in_w
        if im_ratio > model_ratio:
            new_h = in_h
            new_w = int(new_h / im_ratio)
        else:
            new_w = in_w
            new_h = int(new_w * im_ratio)
        scale = new_h / img.shape[0]
        det_img = np.zeros((in_h, in_w, 3), dtype=np.uint8)
        det_img[:new_h, :new_w] = cv2.resize(img, (new_w, new_h))

        scores_l, bboxes_l, kpss_l = self._forward(det_img, self.det_thresh)
        scores = np.concatenate(scores_l) if scores_l else np.empty(0)
        if scores.size == 0:
            return []
        bboxes = np.vstack(bboxes_l) / scale
        kpss = np.vstack(kpss_l) / scale
        pre_det = np.hstack((bboxes, scores[:, None])).astype(np.float32)
        order = np.argsort(-scores, kind="stable")
        pre_det, kpss = pre_det[order], kpss[order]
        keep = _nms(pre_det, self.nms_thresh)
        det, kpss = pre_det[keep], kpss[keep]
        if max_num > 0 and det.shape[0] > max_num:
            det, kpss = det[:max_num], kpss[:max_num]
        return [Face(bbox=d[:4], score=float(d[4]), kps=k) for d, k in zip(det, kpss)]

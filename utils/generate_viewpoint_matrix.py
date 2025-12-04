import numpy as np
import math
import argparse
from tqdm import tqdm
import os

def generate_viewpoint_matrix():
    # 1. 参数设置
    width = 3840
    height = 2160
    num_views = 60
    
    # PE = 20/3
    pe = 20.0 / 3.0
    
    # theta = arctan(1/9) => tan(theta) = 1/9
    # 原始代码中 TG_LENS = math.tan(ANGLE)
    tg_lens = 1.0 / 9.0
    
    offset = 0.0
    
    print(f"生成视点矩阵参数:")
    print(f"分辨率: {width}x{height}")
    print(f"视点数: {num_views}")
    print(f"PE (Pitch): {pe:.4f}")
    print(f"Tan(Theta): {tg_lens:.4f}")
    print(f"Offset: {offset}")

    # 2. 准备结果矩阵
    # 形状为 (Height, Width, 3)，对应每个像素的 R, G, B 三个子像素
    # 使用 uint8 节省内存 (视点数60 < 255)
    viewpoint_matrix = np.zeros((height, width, 3), dtype=np.uint8)

    # 3. 计算逻辑 (按行处理以使用 tqdm 显示进度)
    # D = 3.0 * j + 3.0 * i * TG_LENS + k + OFFSET
    # j 是列索引 (0..width-1)
    # k 是颜色通道 (0,1,2)
    
    # 预先计算列索引和颜色通道部分，这部分对于每一行都是一样的
    # shape: (1, width, 3)
    # j_indices: [[0, 1, 2, ..., width-1]]
    j_indices = np.arange(width).reshape(1, width)
    # k_indices: [[[0, 1, 2]]]
    k_indices = np.array([0, 1, 2]).reshape(1, 1, 3)
    
    # base_val = 3.0 * j + k + offset
    # shape broadcasting: (1, width, 1) + (1, 1, 3) -> (1, width, 3)
    base_values = (3.0 * j_indices[..., None] + k_indices + offset)

    print("开始计算矩阵...")
    
    for i in tqdm(range(height), desc="Processing rows"):
        # 计算当前行的垂直偏移
        # row_offset = 3.0 * i * TG_LENS
        row_val = 3.0 * i * tg_lens
        
        # D = base_values + row_val
        D = base_values + row_val
        
        # A = D % PE
        A = np.mod(D, pe)
        
        # viewp = floor(A / (PE / img_num))
        # 这一步确定每个子像素属于哪个视点索引
        viewp = np.floor(A / (pe / num_views)).astype(np.uint8)
        
        # 确保索引在 [0, num_views-1] 范围内 (处理浮点误差或边界)
        viewp = np.mod(viewp, num_views)
        
        # 填入矩阵
        viewpoint_matrix[i, :, :] = viewp[0, :, :]

    # 4. 输出结果
    output_filename = "viewpoint_matrix.npy"
    print(f"计算完成，正在保存到 {output_filename} ...")
    np.save(output_filename, viewpoint_matrix)
    print("保存成功!")

    # 验证一下数据分布
    print(f"矩阵形状: {viewpoint_matrix.shape}")
    print(f"视点索引范围: Min={viewpoint_matrix.min()}, Max={viewpoint_matrix.max()}")
    
    # 可选：保存一张可视化图片
    try:
        from PIL import Image
        # 将视点索引归一化到 0-255 以便可视化
        viz_img = (viewpoint_matrix.astype(np.float32) / num_views * 255).astype(np.uint8)
        Image.fromarray(viz_img).save("viewpoint_matrix_viz.png")
        print("已生成可视化预览图: viewpoint_matrix_viz.png")
    except ImportError:
        print("未安装 PIL，跳过生成预览图")

if __name__ == "__main__":
    generate_viewpoint_matrix()


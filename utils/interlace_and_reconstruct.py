import numpy as np
import os
import glob
from PIL import Image, ImageFilter
from tqdm import tqdm
import gc
import math

def main():
    # --- 配置参数 ---
    INPUT_DIR = r"C:\Users\Administrator\Desktop\138"
    OUTPUT_DIR = r"D:\yzy\code\python\light_field_vis\utils"
    RECON_DIR = os.path.join(OUTPUT_DIR, "reconstructed_views")
    RECON_FILTERED_DIR = os.path.join(OUTPUT_DIR, "reconstructed_views_filtered")
    
    # 目标分辨率
    W, H = 1200, 1920
    
    # 交织参数 (来自 pixel_grid.js)
    # PE = 30.0 / 7.0
    # TAN_ANGLE = -1.0 / 14.0
    # OFFSET = 0.0
    PE = 19.1813
    TAN_ANGLE = math.tan(0.2305)
    OFFSET = 14.1171
    NUM_VIEWS = 60
    
    print("=== 光场交织与重构脚本 ===")
    print(f"输入目录: {INPUT_DIR}")
    print(f"输出目录: {OUTPUT_DIR}")
    print(f"目标分辨率: {W}x{H}")
    print(f"视点数: {NUM_VIEWS}")
    print(f"PE: {PE:.4f}, Tan(Angle): {TAN_ANGLE:.4f}")
    
    # 确保输出目录存在
    os.makedirs(RECON_DIR, exist_ok=True)
    os.makedirs(RECON_FILTERED_DIR, exist_ok=True)
    
    # --- 1. 加载并调整图像 ---
    print("\n[1/6] 正在搜索并加载图像...")
    
    exts = ['*.png', '*.jpg', '*.jpeg', '*.bmp']
    files = []
    for ext in exts:
        files.extend(glob.glob(os.path.join(INPUT_DIR, ext)))
    
    try:
        files.sort(key=lambda x: int(''.join(filter(str.isdigit, os.path.basename(x))) or 0))
    except:
        print("警告: 无法按数字排序文件名，将使用默认字典序")
        files.sort()
        
    if len(files) == 0:
        print(f"错误: 在 {INPUT_DIR} 未找到图像文件")
        return

    if len(files) < NUM_VIEWS:
        print(f"警告: 找到的图像数量 ({len(files)}) 少于预期的视点数 ({NUM_VIEWS})")
    
    target_files = files[:NUM_VIEWS]
    print(f"将处理 {len(target_files)} 张图像")
    
    input_images = np.zeros((NUM_VIEWS, H, W, 3), dtype=np.uint8)
    
    for idx, fpath in enumerate(tqdm(target_files, desc="Loading & Resizing")):
        try:
            with Image.open(fpath) as img:
                img = img.convert('RGB')
                if img.size != (W, H):
                    img = img.resize((W, H), Image.Resampling.LANCZOS)
                input_images[idx] = np.array(img)
        except Exception as e:
            print(f"加载图像失败 {fpath}: {e}")

    # --- 2. 生成视点矩阵 (Viewpoint Matrix) ---
    print("\n[2/6] 正在计算视点索引矩阵...")
    
    y_idx = np.arange(H).reshape(H, 1)
    x_idx = np.arange(W).reshape(1, W)
    
    term_x = 3.0 * x_idx
    term_y = 3.0 * y_idx * TAN_ANGLE
    
    base_term = term_x + term_y + OFFSET 
    step = PE / NUM_VIEWS
    
    view_matrix = np.zeros((H, W, 3), dtype=np.uint8)
    
    for c in range(3):
        D = base_term + c
        A = np.mod(D, PE)
        V = np.floor(A / step).astype(np.int32)
        V = np.mod(V, NUM_VIEWS)
        view_matrix[:, :, c] = V.astype(np.uint8)
        
    print("视点矩阵计算完成")

    # --- 3. 执行交织 (Interlacing) ---
    print("\n[3/6] 正在执行像素交织...")
    
    interlaced_image = np.zeros((H, W, 3), dtype=np.uint8)
    grid_y, grid_x = np.indices((H, W))
    
    for c in tqdm(range(3), desc="Processing Channels"):
        views = view_matrix[:, :, c]
        interlaced_image[:, :, c] = input_images[views, grid_y, grid_x, c]
        
    output_path = os.path.join(OUTPUT_DIR, "interlaced_result.png")
    print(f"保存交织图像到: {output_path}")
    Image.fromarray(interlaced_image).save(output_path)
    
    # --- 4. 释放内存 ---
    del input_images
    gc.collect()
    print("已释放原始图像内存")
    
    # --- 5. 反向重构 (Reconstruction) & 统计 ---
    print("\n[5/6] 正在反向重构视点图并统计像素分布...")
    
    # 统计每个视点被还原的 R, G, B 像素数 (填充值不为0的其实在这里不准确，
    # 准确的说是该位置是否归属于该视点)
    # 我们统计 mask 的数量
    pixel_stats = [] # 存储每个视点的 [R_count, G_count, B_count]
    
    recon_images = np.zeros((NUM_VIEWS, H, W, 3), dtype=np.uint8)
    
    for v in tqdm(range(NUM_VIEWS), desc="Reconstructing"):
        stats = [0, 0, 0] # R, G, B count
        for c in range(3):
            # 找到属于视点 v 的像素掩码
            mask = (view_matrix[:, :, c] == v)
            count = np.sum(mask)
            stats[c] = int(count)
            
            if count > 0:
                recon_images[v, :, :, c][mask] = interlaced_image[:, :, c][mask]
        
        pixel_stats.append(stats)
    
    # 打印统计信息
    print("\n--- 像素还原统计 (每个视点拥有的有效像素数) ---")
    total_pixels = W * H
    print(f"单张图总像素数 (单通道): {total_pixels}")
    
    header = f"{'View ID':^10} | {'R Count':^12} | {'G Count':^12} | {'B Count':^12} | {'Empty (All Ch)':^15}"
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    
    for v, stats in enumerate(pixel_stats):
        # Empty 是指该视点在该位置没有分配到任何像素
        # 对于单个通道来说，Empty = Total - Count
        # 这里展示每个通道的空缺数可能太多，我们只打印填充数
        # 或者打印每个通道的空缺数
        
        # 为了简洁，Empty (All Ch) 显示三个通道总共空缺的子像素数
        total_slots = total_pixels * 3
        filled_slots = sum(stats)
        empty_slots = total_slots - filled_slots
        
        print(f"{v+1:^10} | {stats[0]:^12} | {stats[1]:^12} | {stats[2]:^12} | {empty_slots:^15}")
        
    print("-" * len(header))
    
    # --- 6. 保存结果 (原图 & 滤波图) ---
    print("\n[6/6] 正在保存重构图像 (原始 & 均值滤波)...")
    
    for v in tqdm(range(NUM_VIEWS), desc="Saving & Filtering"):
        # 1. 保存原始重构图 (含大量黑色零点)
        img_pil = Image.fromarray(recon_images[v])
        save_path = os.path.join(RECON_DIR, f"recon_view_{v+1:03d}.png")
        img_pil.save(save_path)
        
        # 2. 智能插值处理 (填补空洞)
        # 原始图像非常稀疏，大量像素为 0。普通的均值滤波(BoxBlur)会将黑色也平均进去，导致图像极度变暗。
        # 我们需要一种"扩散"机制，将有效像素的值填补到周围的黑色区域。
        # 
        # 方案：形态学膨胀 (Dilation) / MaxFilter
        # 原理：在邻域内取最大值。因为背景是黑色(0)，有效像素>0，
        # 取最大值可以直接把彩色像素“扩散”到周围的黑色区域，且保持亮度不变。
        # 半径选择：视点数60，意味着平均每60个像素才有一个值。
        # PE ≈ 4.3，说明水平方向每4-5个像素有一个有效值。
        # 垂直方向也有偏移。
        # 考虑到稀疏度，我们需要一个较大的核来覆盖空隙。尝试 size=5 (半径2) 或 size=7 (半径3)。
        
        # 使用 MaxFilter (3x3 或 5x5)
        # 迭代两次小的膨胀通常比一次大的膨胀效果更自然
        
        # 第一次膨胀：填补主要空隙
        filtered_img = img_pil.filter(ImageFilter.MaxFilter(size=5))
        
        # 可选：再做一次小的均值滤波平滑一下锯齿 (此时黑色空洞已基本填满，BoxBlur不会导致太黑)
        # filtered_img = filtered_img.filter(ImageFilter.BoxBlur(radius=1))
        
        save_path_filtered = os.path.join(RECON_FILTERED_DIR, f"recon_view_{v+1:03d}_filled.png")
        filtered_img.save(save_path_filtered)
        
    print("\n全部完成!")
    print(f"交织结果: {output_path}")
    print(f"重构视点 (原始): {RECON_DIR}")
    print(f"重构视点 (滤波): {RECON_FILTERED_DIR}")

if __name__ == "__main__":
    main()

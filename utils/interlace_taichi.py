import math
import numpy as np
import taichi as ti
from PIL import Image
import os
from datetime import datetime
import argparse
import time

# 初始化命令行参数解析
parser = argparse.ArgumentParser(description='光场编码图像生成器')
parser.add_argument('--folder', type=str, required=True, 
                    help='包含输入图像的文件夹路径')
parser.add_argument('--img_num', type=int, required=True,
                    help='输入图像的数量')
parser.add_argument('--reverse', action='store_true',
                    help='是否反转图像顺序（默认不反转）')
parser.add_argument('--width', type=int, default=7680,
                    help='输出图像宽度（默认7680）')
parser.add_argument('--height', type=int, default=4320,
                    help='输出图像高度（默认4320）')
parser.add_argument('--angle', type=float, default=0.106395,
                    help='倾角弧度（默认0.106395）')
parser.add_argument('--pe', type=float, default=16.7240,
                    help='线数（默认16.7240）')
parser.add_argument('--offset', type=float, default=12.5,
                    help='偏移量（默认9.6）')
parser.add_argument('--output', type=str, default=None,
                    help='输出文件名（默认使用时间戳）')
parser.add_argument('--gpu', action='store_true',
                    help='使用GPU加速（默认使用CPU）')
args = parser.parse_args()

# 初始化Taichi
ti.init(arch=ti.gpu if args.gpu else ti.cpu, default_fp=ti.f64)

# 屏幕参数设置
SCREEN_WIDTH, SCREEN_HEIGHT = args.width, args.height
ANGLE = args.angle
PE = args.pe
OFFSET = args.offset
TG_LENS = math.tan(ANGLE)

# 从命令行参数获取输入
input_folder = args.folder
img_num = args.img_num
reverse_order = args.reverse

print(f"正在处理文件夹: {input_folder}")
print(f"预期图像数量: {img_num}")
print(f"输出分辨率: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
print(f"图像顺序反转: {'是' if reverse_order else '否'}")
print(f"使用{'GPU' if args.gpu else 'CPU'}进行计算")

def read_images_from_folder(folder_path, expected_num, target_width, target_height, reverse=False):
    """从文件夹读取图像并调整大小"""
    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"文件夹不存在: {folder_path}")
    
    # 获取并过滤图像文件
    filenames = [f for f in os.listdir(folder_path) 
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp'))]
    
    if len(filenames) == 0:
        raise ValueError("未找到支持的图像文件 (.jpg, .png, .bmp)")
    
    # 按文件名排序
    try:
        filenames.sort(key=lambda x: int(x.split('.')[0]))
    except (ValueError, IndexError):
        print("无法按数字排序文件名，使用字母顺序排序")
        filenames.sort()
    
    # 如果指定了反转顺序，则反转文件名列表
    if reverse:
        filenames = filenames[::-1]
        print("已反转图像加载顺序")
    
    # 限制文件数量
    filenames = filenames[:expected_num]
    actual_num = len(filenames)
    
    print(f"找到 {actual_num} 张图像，正在加载...")
    
    # 为了避免内存问题，逐个处理图像
    image_list = []
    for idx, filename in enumerate(filenames):
        image_path = os.path.join(folder_path, filename)
        try:
            with Image.open(image_path) as img:
                image = img.convert("RGB")
                tile = np.array(image.resize((target_width, target_height)))
                image_list.append(tile)
            print(f"已加载: {filename} ({idx+1}/{actual_num})")
        except Exception as e:
            print(f"加载图像 {filename} 时出错: {str(e)}")
    
    return np.array(image_list), actual_num

def process_with_taichi_tiled(tiles_data, img_num, height, width):
    """使用Taichi的分块处理方法，确保索引在int32范围内"""
    # 计算合适的块大小，确保索引不会超出int32范围
    # 对于8K分辨率，我们可以选择较小的块大小
    tile_height = 1080  # 可以根据需要调整，确保索引不超过int32限制
    tile_width = width  # 宽度通常不会导致索引问题
    
    # 创建结果数组
    result = np.zeros((height, width, 3), dtype=np.uint8)
    
    # 创建Taichi字段
    tile_tiles = ti.Vector.field(3, dtype=ti.u8, shape=(img_num, tile_height, tile_width))
    tile_result = ti.Vector.field(3, dtype=ti.u8, shape=(tile_height, tile_width))
    
    @ti.kernel
    def compute_tile(start_row: ti.i32, img_num_val: ti.i32):
        for i, j in tile_result:
            # 计算实际行索引
            real_i = i + start_row
            
            for k in ti.static(range(3)):
                # 计算视点
                D = 3.0 * j + 3.0 * real_i * TG_LENS + k + OFFSET
                A = D % PE
                if A < 0:
                    A += PE
                viewp_float = A / (PE / img_num_val)
                viewp = ti.cast(ti.floor(viewp_float), ti.i32)
                
                # 使用取模操作确保viewp在有效范围内
                # 这里使用ti.i32类型，确保不会超出int32范围
                viewp = viewp % img_num_val
                
                # 获取对应像素
                tile_result[i, j][k] = tile_tiles[viewp, i, j][k]
    
    # 按块处理图像
    for start_row in range(0, height, tile_height):
        end_row = min(start_row + tile_height, height)
        current_height = end_row - start_row
        
        print(f"处理行 {start_row+1}-{end_row} / {height}")
        
        # 如果当前块高度不等于tile_height，重新创建字段
        if current_height != tile_height:
            tile_tiles = ti.Vector.field(3, dtype=ti.u8, shape=(img_num, current_height, tile_width))
            tile_result = ti.Vector.field(3, dtype=ti.u8, shape=(current_height, tile_width))
        
        # 将当前块数据加载到Taichi
        tile_tiles.from_numpy(tiles_data[:, start_row:end_row, :, :])
        
        # 处理当前块
        compute_tile(start_row, img_num)
        
        # 将结果复制回NumPy数组
        result[start_row:end_row, :, :] = tile_result.to_numpy()
    
    return result

def process_with_taichi_color_channels(tiles_data, img_num, height, width):
    """按颜色通道分别处理，进一步减小索引范围"""
    # 创建结果数组
    result = np.zeros((height, width, 3), dtype=np.uint8)
    
    # 每次处理一个颜色通道
    for channel in range(3):
        print(f"处理颜色通道 {channel+1}/3")
        
        # 创建单通道Taichi字段
        channel_tiles = ti.field(dtype=ti.u8, shape=(img_num, height, width))
        channel_result = ti.field(dtype=ti.u8, shape=(height, width))
        
        # 将单通道数据加载到Taichi
        channel_tiles.from_numpy(tiles_data[:, :, :, channel])
        
        @ti.kernel
        def compute_channel(channel_val: ti.i32, img_num_val: ti.i32):
            for i, j in channel_result:
                # 计算视点
                D = 3.0 * j + 3.0 * i * TG_LENS + channel_val + OFFSET
                A = D % PE
                if A < 0:
                    A += PE
                viewp_float = A / (PE / img_num_val)
                viewp = ti.cast(ti.floor(viewp_float), ti.i32)
                
                # 使用取模操作确保viewp在有效范围内
                viewp = viewp % img_num_val
                
                # 获取对应像素
                channel_result[i, j] = channel_tiles[viewp, i, j]
        
        # 处理当前通道
        compute_channel(channel, img_num)
        
        # 将结果复制回NumPy数组
        result[:, :, channel] = channel_result.to_numpy()
    
    return result

def process_with_taichi_hybrid(tiles_data, img_num, height, width):
    """结合分块和颜色通道处理的混合方法"""
    # 计算合适的块大小
    tile_height = 1080  # 可以根据需要调整
    
    # 创建结果数组
    result = np.zeros((height, width, 3), dtype=np.uint8)
    
    # 按块和通道处理图像
    for start_row in range(0, height, tile_height):
        end_row = min(start_row + tile_height, height)
        current_height = end_row - start_row
        
        print(f"处理行 {start_row+1}-{end_row} / {height}")
        
        # 每次处理一个颜色通道
        for channel in range(3):
            # 创建单通道Taichi字段
            channel_tiles = ti.field(dtype=ti.u8, shape=(img_num, current_height, width))
            channel_result = ti.field(dtype=ti.u8, shape=(current_height, width))
            
            # 将单通道数据加载到Taichi
            channel_tiles.from_numpy(tiles_data[:, start_row:end_row, :, channel])
            
            @ti.kernel
            def compute_tile_channel(start_row_val: ti.i32, channel_val: ti.i32, img_num_val: ti.i32):
                for i, j in channel_result:
                    # 计算实际行索引
                    real_i = i + start_row_val
                    
                    # 计算视点
                    D = 3.0 * j + 3.0 * real_i * TG_LENS + channel_val + OFFSET
                    A = D % PE
                    if A < 0:
                        A += PE
                    viewp_float = A / (PE / img_num_val)
                    viewp = ti.cast(ti.floor(viewp_float), ti.i32)
                    
                    # 使用取模操作确保viewp在有效范围内
                    viewp = viewp % img_num_val
                    
                    # 获取对应像素
                    channel_result[i, j] = channel_tiles[viewp, i, j]
            
            # 处理当前块的当前通道
            compute_tile_channel(start_row, channel, img_num)
            
            # 将结果复制回NumPy数组
            result[start_row:end_row, :, channel] = channel_result.to_numpy()
    
    return result

# 主处理流程
try:
    start_time = time.time()
    
    # 读取图像
    print("开始读取图像...")
    tiles_np, actual_img_num = read_images_from_folder(
        input_folder, img_num, SCREEN_WIDTH, SCREEN_HEIGHT, reverse_order
    )
    
    # 更新实际图像数量
    if actual_img_num != img_num:
        print(f"警告: 实际加载图像数量 ({actual_img_num}) 与预期 ({img_num}) 不一致")
        img_num = actual_img_num
    
    # 转换数据形状
    tiles_np = tiles_np.reshape((img_num, SCREEN_HEIGHT, SCREEN_WIDTH, 3))
    
    # 处理图像
    print("开始处理图像...")
    
    # 根据图像大小选择最合适的处理方法
    if SCREEN_WIDTH * SCREEN_HEIGHT > 4000000:  # 大于4M像素的大图像
        print("检测到大尺寸图像，使用混合处理方法...")
        result = process_with_taichi_hybrid(tiles_np, img_num, SCREEN_HEIGHT, SCREEN_WIDTH)
    elif img_num > 30:  # 图像数量较多
        print("检测到较多图像，使用颜色通道分离处理方法...")
        result = process_with_taichi_color_channels(tiles_np, img_num, SCREEN_HEIGHT, SCREEN_WIDTH)
    else:  # 其他情况
        print("使用分块处理方法...")
        result = process_with_taichi_tiled(tiles_np, img_num, SCREEN_HEIGHT, SCREEN_WIDTH)
    
    # 保存结果
    if args.output:
        output_file = args.output
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"code_image_{timestamp}.bmp"
    
    Image.fromarray(result).save(output_file)
    
    elapsed_time = time.time() - start_time
    print(f"处理完成! 结果已保存至: {output_file}")
    print(f"总处理时间: {elapsed_time:.2f} 秒")
    
except Exception as e:
    print(f"处理过程中发生错误: {str(e)}")
    import traceback
    traceback.print_exc()
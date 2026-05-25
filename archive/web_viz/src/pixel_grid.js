
// 参数状态
const state = {
    scale: 40,        // 像素大小 (屏幕像素/物理像素)
    offsetX: 20,      // 画布偏移 X
    offsetY: 20,      // 画布偏移 Y
    pe: 30.0 / 7.0,   // 节距
    tanAngle:  -1.0 / 14.0, // tan(theta)
    offset: 0.0,      // 初始偏移
    numViews: 60,     // 视点数
    lensOpacity: 0.4, // 透镜不透明度
    
    // 鼠标状态
    isDragging: false,
    lastMouseX: 0,
    lastMouseY: 0,
    hoverX: -1,
    hoverY: -1,
    hoverSub: -1 // 0:R, 1:G, 2:B
};

const canvas = document.getElementById('gridCanvas');
const ctx = canvas.getContext('2d');
const container = document.getElementById('canvas-container');
const hoverInfo = document.getElementById('hoverInfo');

// 初始化 DOM 绑定
function init() {
    resize();
    window.addEventListener('resize', resize);
    
    // 鼠标交互
    container.addEventListener('mousedown', e => {
        state.isDragging = true;
        state.lastMouseX = e.clientX;
        state.lastMouseY = e.clientY;
        container.style.cursor = 'grabbing';
    });
    
    window.addEventListener('mouseup', () => {
        state.isDragging = false;
        container.style.cursor = 'grab';
    });
    
    container.addEventListener('mousemove', e => {
        if (state.isDragging) {
            const dx = e.clientX - state.lastMouseX;
            const dy = e.clientY - state.lastMouseY;
            state.offsetX += dx;
            state.offsetY += dy;
            state.lastMouseX = e.clientX;
            state.lastMouseY = e.clientY;
            requestRender();
        }
        
        // 计算 Hover 的像素坐标
        updateHover(e.clientX, e.clientY);
    });
    
    container.addEventListener('wheel', e => {
        e.preventDefault();
        const zoomSpeed = 0.001;
        const zoom = Math.exp(-e.deltaY * zoomSpeed);
        
        // 以鼠标为中心缩放
        // newScale = oldScale * zoom
        // (mouseX - offsetX) / oldScale = worldX = (mouseX - newOffsetX) / newScale
        // newOffsetX = mouseX - (mouseX - offsetX) * zoom
        
        const mouseX = e.clientX;
        const mouseY = e.clientY;
        
        const nextScale = Math.max(5, Math.min(200, state.scale * zoom));
        const actualZoom = nextScale / state.scale;
        
        state.offsetX = mouseX - (mouseX - state.offsetX) * actualZoom;
        state.offsetY = mouseY - (mouseY - state.offsetY) * actualZoom;
        state.scale = nextScale;
        
        // 同步 Slider
        document.getElementById('scaleSlider').value = state.scale;
        
        requestRender();
    }, { passive: false });

    // UI 控件绑定
    bindInput('scaleSlider', v => {
        // 以屏幕中心缩放
        const cx = canvas.width / 2;
        const cy = canvas.height / 2;
        const nextScale = parseFloat(v);
        const actualZoom = nextScale / state.scale;
        state.offsetX = cx - (cx - state.offsetX) * actualZoom;
        state.offsetY = cy - (cy - state.offsetY) * actualZoom;
        state.scale = nextScale;
    });
    bindInput('peInput', v => state.pe = parseFloat(v));
    bindInput('angleInput', v => state.tanAngle = parseFloat(v));
    bindInput('offsetInput', v => state.offset = parseFloat(v));
    bindInput('viewsInput', v => state.numViews = parseInt(v));
    bindInput('lensOpacity', v => state.lensOpacity = parseFloat(v));

    requestRender();
}

function bindInput(id, callback) {
    const el = document.getElementById(id);
    el.addEventListener('input', (e) => {
        callback(e.target.value);
        requestRender();
    });
}

function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    requestRender();
}

function updateHover(mx, my) {
    // World coordinates
    // screenX = worldX * scale + offsetX
    // worldX = (screenX - offsetX) / scale
    
    // 注意：这里的 worldX 单位是“像素个数”，worldY 同理
    const wx = (mx - state.offsetX) / state.scale;
    const wy = (my - state.offsetY) / state.scale;
    
    const ix = Math.floor(wx);
    const iy = Math.floor(wy);
    
    // 计算子像素索引 (0, 1, 2)
    // 每一个像素被分成三份 [0, 0.33), [0.33, 0.66), [0.66, 1.0)
    const subX = (wx - ix) * 3;
    const k = Math.floor(subX); 
    
    if (k >= 0 && k <= 2) {
        state.hoverX = ix;
        state.hoverY = iy;
        state.hoverSub = k;
        
        // 计算 View ID
        // D = 3*x + 3*y*tan + k + offset
        const D = 3 * ix + 3 * iy * state.tanAngle + k + state.offset;
        let A = D % state.pe;
        if (A < 0) A += state.pe;
        const viewP = Math.floor(A / (state.pe / state.numViews)) % state.numViews;
        
        const colors = ['R', 'G', 'B'];
        hoverInfo.innerText = `Pixel: (${ix}, ${iy})\nSub: ${colors[k]}\nView ID: ${viewP}`;
    } else {
        state.hoverX = -1;
        hoverInfo.innerText = "-";
    }
    
    // 如果不想每次移动都重绘整个画面（比较耗费），可以只在需要高亮变化时重绘
    // 这里简单起见，每次都重绘（现代浏览器 Canvas 性能通常够用）
    requestRender();
}

let animationFrame = null;
function requestRender() {
    if (!animationFrame) {
        animationFrame = requestAnimationFrame(() => {
            render();
            animationFrame = null;
        });
    }
}

function render() {
    // 清空
    ctx.fillStyle = '#111';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    const { scale, offsetX, offsetY, pe, tanAngle, offset, numViews, lensOpacity } = state;
    
    // 计算可见区域 (Viewport culling)
    // 0 = minX * scale + offsetX => minX = -offsetX / scale
    const startX = Math.floor(-offsetX / scale);
    const endX = Math.ceil((canvas.width - offsetX) / scale);
    const startY = Math.floor(-offsetY / scale);
    const endY = Math.ceil((canvas.height - offsetY) / scale);
    
    // 1. 绘制像素网格和子像素
    const subPixelWidth = scale / 3.0;
    
    for (let y = startY; y < endY; y++) {
        // 每一行的 Y 偏移贡献
        // D_row = 3 * y * tanAngle
        const D_row = 3 * y * tanAngle;
        const py = y * scale + offsetY;
        
        for (let x = startX; x < endX; x++) {
            const px = x * scale + offsetX;
            
            // D_base = 3 * x + D_row + offset
            const D_base = 3 * x + D_row + offset;
            
            // Draw 3 sub-pixels
            for (let k = 0; k < 3; k++) {
                const D = D_base + k;
                
                // Calc View ID
                let A = D % pe;
                if (A < 0) A += pe;
                const viewId = Math.floor(A / (pe / numViews)) % numViews;
                
                // Color based on Sub-pixel (R/G/B) but dimmed by View ID to differentiate?
                // User request: "RGB就用红绿蓝表示就行" -> pure R/G/B colors?
                // But we also need to see the View ID pattern.
                // If we use pure RGB, we lose the "View ID" visualization via color.
                // But the text now shows View ID.
                
                // Let's use R/G/B base colors, and modulate brightness/saturation by View ID
                // to make the pattern visible, or just pure R/G/B.
                // "RGB就用红绿蓝表示就行" -> implies the background color of the subpixel should be Red, Green, or Blue.
                
                let color;
                // Base colors for subpixels - Pure standard RGB
                if (k === 0) { // R
                    color = '#FF4444'; 
                } else if (k === 1) { // G
                    color = '#44FF44';
                } else { // B
                    color = '#4444FF';
                }
                
                ctx.fillStyle = color;
                ctx.fillRect(px + k * subPixelWidth, py, subPixelWidth, scale);
                
                // 高亮 Hover
                if (x === state.hoverX && y === state.hoverY && k === state.hoverSub) {
                    ctx.strokeStyle = 'white';
                    ctx.lineWidth = 2;
                    ctx.strokeRect(px + k * subPixelWidth + 1, py + 1, subPixelWidth - 2, scale - 2);
                }
            }
            
            // 绘制像素边框 (Grid)
            ctx.strokeStyle = '#333';
            ctx.lineWidth = 1;
            ctx.strokeRect(px, py, scale, scale);
        }
    }
    
    // 2. 绘制子像素标识 (RGB) - 只在缩放够大时显示
    if (scale > 20) {
        ctx.font = `bold ${scale * 0.3}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        
        const labelColor = '#000000';
        
        for (let y = startY; y < endY; y++) {
            const D_row = 3 * y * tanAngle;
            const py = y * scale + offsetY;
            for (let x = startX; x < endX; x++) {
                const px = x * scale + offsetX;
                const D_base = 3 * x + D_row + offset;
                
                for (let k = 0; k < 3; k++) {
                    // Re-calculate viewId for text label
                    const D = D_base + k;
                    let A = D % pe;
                    if (A < 0) A += pe;
                    const viewId = Math.floor(A / (pe / numViews)) % numViews;
                    
                    ctx.fillStyle = labelColor;
                    ctx.fillText(viewId, px + k * subPixelWidth + subPixelWidth/2, py + scale/2);
                }
            }
        }
    }
    
    // 3. 绘制透镜 (Lenticular Lens Overlay)
    if (lensOpacity > 0) {
        // 透镜边界方程: u + 3v*tan = n * PE - offset
        // u 是水平物理坐标 (1 unit = 1 sub-pixel width)
        // 屏幕坐标 screenX = (u / 3) * scale + offsetX
        // => u = (screenX - offsetX) / scale * 3
        // screenY = v * scale + offsetY => v = (screenY - offsetY) / scale
        
        // 我们可以直接在物理空间计算直线的端点，然后转换到屏幕空间绘制
        
        // 可见物理区域
        // u_min = startX * 3, u_max = endX * 3
        // v_min = startY, v_max = endY
        
        // 我们需要找到所有的 n，使得直线穿过这个区域
        // n * PE = u + 3v*tan + offset
        // 计算 n 的范围
        // min_val = u_min + 3*v_min*tan + offset
        // max_val = u_max + 3*v_max*tan + offset (假设 tan > 0)
        
        // 为了简化，覆盖稍微大一点的范围
        const u_min = startX * 3;
        const u_max = endX * 3;
        const v_min = startY;
        const v_max = endY;
        
        // Corner values to find min/max phase
        const corners = [
            {u: u_min, v: v_min},
            {u: u_max, v: v_min},
            {u: u_min, v: v_max},
            {u: u_max, v: v_max}
        ];
        
        let minPhase = Infinity;
        let maxPhase = -Infinity;
        
        corners.forEach(p => {
            const ph = p.u + 3 * p.v * tanAngle + offset;
            if (ph < minPhase) minPhase = ph;
            if (ph > maxPhase) maxPhase = ph;
        });
        
        const n_start = Math.floor(minPhase / pe);
        const n_end = Math.ceil(maxPhase / pe);
        
        ctx.lineWidth = 2;
        
        for (let n = n_start; n <= n_end; n++) {
            // 绘制第 n 个透镜的左边界 (Phase = 0)
            // u + 3v*tan + offset = n * PE
            // u = n * PE - offset - 3v*tan
            
            // 我们需要在 v_min 和 v_max 处找到 u，画线
            const rhs = n * pe - offset;
            
            const u1 = rhs - 3 * v_min * tanAngle;
            const u2 = rhs - 3 * v_max * tanAngle;
            
            // 转换屏幕坐标 (Left Edge P1)
            const sx1 = (u1 / 3) * scale + offsetX;
            const sy1 = v_min * scale + offsetY;
            
            // Right Edge P2 (on the same scanline sy1)
            const next_rhs = (n + 1) * pe - offset;
            const u1_next = next_rhs - 3 * v_min * tanAngle;
            const sx1_n = (u1_next / 3) * scale + offsetX;
            
            // ------------------------------------------------
            // 高级玻璃材质渲染 (Advanced Glass Shader)
            // ------------------------------------------------
            
            // 1. 计算渐变向量 (Gradient Vector)
            // 为了让光影贴合斜透镜，渐变方向应垂直于透镜轴。
            // 透镜轴向量 (屏幕空间): V_axis = (-tanAngle, 1)  (Assuming square pixels approx)
            // 垂直向量: V_perp = (1, tanAngle)
            // 我们需要定义一个 LinearGradient，起点在左边界，终点在右边界，且方向沿 V_perp。
            // 简单做法：取左上角点 P_start(sx1, sy1)，计算它在垂直方向投影到右边界的长度。
            // 水平宽度 W = sx1_n - sx1
            // 渐变向量的水平分量 dx = W
            // 渐变向量的垂直分量 dy = W * tanAngle
            // 这样定义出来的渐变就会以此斜率填充。
            
            const W = sx1_n - sx1;
            const grad_dx = W;
            const grad_dy = W * tanAngle;
            
            const gradient = ctx.createLinearGradient(sx1, sy1, sx1 + grad_dx, sy1 + grad_dy);
            
            // 2. 配置玻璃质感 (Glass Material Profile)
            // 模拟圆柱面的 Fresnel 效应和高光
            // 假设光源在左上方
            
            // 0.0 - 0.1: 左边缘阴影 (Occlusion) + 轮廓
            gradient.addColorStop(0.00, `rgba(30, 40, 50, ${Math.min(1, lensOpacity * 1.5)})`); 
            
            // 0.15 - 0.35: 主高光 (Main Specular Highlight) - 锐利且明亮
            // 玻璃的高光通常很锐
            gradient.addColorStop(0.15, `rgba(255, 255, 255, 0.05)`);
            gradient.addColorStop(0.22, `rgba(255, 255, 255, ${Math.min(1, lensOpacity * 0.8)})`); // Highlight Peak (Lowered)
            gradient.addColorStop(0.30, `rgba(255, 255, 255, 0.05)`);
            
            // 0.5: 材质本体 (Body) - 最透，带一点青色玻璃底色
            gradient.addColorStop(0.50, `rgba(200, 240, 255, ${lensOpacity * 0.1})`);
            
            // 0.85: 次级反光 / 边缘光 (Rim Light)
            gradient.addColorStop(0.80, `rgba(220, 245, 255, 0.1)`);
            gradient.addColorStop(0.92, `rgba(255, 255, 255, ${Math.min(1, lensOpacity * 0.8)})`);
            
            // 1.0: 右边缘阴影
            gradient.addColorStop(1.00, `rgba(30, 40, 50, ${Math.min(1, lensOpacity * 1.5)})`);
            
            // 3. 绘制透镜体
            ctx.fillStyle = gradient;
            ctx.beginPath();
            ctx.moveTo(sx1, sy1);
            // Top-Right
            ctx.lineTo(sx1_n, sy1);
            // Bottom-Right
            const sx2_n = ((next_rhs - 3 * v_max * tanAngle) / 3) * scale + offsetX;
            const sy2 = v_max * scale + offsetY;
            ctx.lineTo(sx2_n, sy2);
            // Bottom-Left
            const sx2 = ((n * pe - offset - 3 * v_max * tanAngle) / 3) * scale + offsetX;
            ctx.lineTo(sx2, sy2);
            ctx.closePath();
            ctx.fill();
            
            // 4. 绘制边缘线 (微弱的轮廓)
            ctx.strokeStyle = `rgba(255, 255, 255, ${lensOpacity * 0.3})`;
            ctx.lineWidth = 1;
            ctx.stroke();
            
            // 5. 光轴 (Optical Axis) - 虚线，辅助对齐
            if (scale > 15) { // 只有放大时才显示光轴，以免缩小太乱
                const mid_rhs = (n + 0.5) * pe - offset;
                const u1_mid = mid_rhs - 3 * v_min * tanAngle;
                const u2_mid = mid_rhs - 3 * v_max * tanAngle;
                const sx1_mid = (u1_mid / 3) * scale + offsetX;
                const sx2_mid = (u2_mid / 3) * scale + offsetX;
                
                ctx.strokeStyle = `rgba(255, 255, 255, ${lensOpacity * 0.5})`;
                ctx.lineWidth = 1;
                ctx.setLineDash([5, 5]);
                ctx.beginPath();
                ctx.moveTo(sx1_mid, sy1);
                ctx.lineTo(sx2_mid, sy2);
                ctx.stroke();
                ctx.setLineDash([]);
            }
        }
    }
}

init();


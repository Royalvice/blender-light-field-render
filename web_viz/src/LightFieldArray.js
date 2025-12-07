import * as THREE from 'three';

export class LightFieldArray {
    constructor(scene) {
        this.scene = scene;
        this.group = new THREE.Group();
        this.scene.add(this.group);
        
        this.cameras = [];
        this.helpers = [];
        this.cameraMeshes = [];
        this.rayLines = null;
        
        // Materials
        // Focal plane will now use a texture, so we use white color to modulate
        this.focalPlaneMat = new THREE.MeshBasicMaterial({ 
            color: 0xffffff, 
            transparent: true, 
            opacity: 0.95, 
            side: THREE.DoubleSide,
            wireframe: false
        });
        this.focalPlaneBorderMat = new THREE.LineBasicMaterial({ color: 0x444444 }); // Dark border
        
        this.cubeMat = new THREE.LineBasicMaterial({ color: 0x0088ff, transparent: true, opacity: 0.5 }); // Bright Blue
        
        this.cameraBodyGeo = new THREE.BoxGeometry(0.1, 0.1, 0.2);
        this.cameraBodyMat = new THREE.MeshStandardMaterial({ color: 0x666666, metalness: 0.6, roughness: 0.4 }); // Lighter Metallic

        // Objects
        this.focalPlaneMesh = null;
        this.displayCubeHelper = null;
        
        // Ray Visualization Objects
        this.rayGroup = new THREE.Group();
        this.group.add(this.rayGroup);
        this.singleRayLine = null; // For hover interaction
        
        // Current Parameters
        this.currentParams = {};
        this.focalPlaneMode = 'ViewMap'; // 'ViewMap' or 'Lit'
        this.visState = {
            mode: 'Active', // 'Active' or 'All'
            channel: 'All'  // 'All', 'Red', 'Green', 'Blue'
        };
        this.lenticularParams = {
            pe: 19.1813,
            tanAngle: Math.tan(0.2305),
            offset: 14.1171,
            numViews: 60,
            resW: 32,
            resH: 32
        };
    }

    setFocalPlaneMode(mode) {
        if (this.focalPlaneMode !== mode) {
            this.focalPlaneMode = mode;
            this.updateFocalPlaneTexture();
        }
    }

    setRayVisualizationState(mode, channel) {
        let changed = false;
        if (this.visState.mode !== mode) {
            this.visState.mode = mode;
            changed = true;
        }
        if (this.visState.channel !== channel) {
            this.visState.channel = channel;
            changed = true;
        }
        
        if (changed && this.focalPlaneMode === 'Lit') {
            this.updateFocalPlaneTexture();
        }
    }

    update(params) {
        this.currentParams = params;
        this.lenticularParams.numViews = params.N; // Sync numViews with camera count

        // Clean up existing
        this.group.clear();
        this.group.add(this.rayGroup); // Keep ray group
        this.rayGroup.clear(); // Clear rays inside
        this.cameras = [];
        this.helpers = [];
        this.cameraMeshes = [];

        const {
            N, // Camera count
            d_f, // Focal distance
            W_array, // Array width
            fov_s, // Horizontal FOV in degrees
            aspect, // rs
            near,
            far,
            d_cube, // Display cube depth
            showFrustums,
            activeCameraIndex
        } = params;

        // 1. Calculate Derived Parameters
        // W_f: Focal Plane Width
        // W_f = 2 * d_f * tan(fov/2)
        const fovRad = THREE.MathUtils.degToRad(fov_s);
        const W_f = 2 * d_f * Math.tan(fovRad / 2);
        const H_f = W_f / aspect;

        this.W_f = W_f;
        this.H_f = H_f;

        // --- Feature 1: Generate Focal Plane Texture ---
        this.updateFocalPlaneTexture();

        // 2. Visualize Focal Plane (at origin)
        // The focal plane is centered at (0,0,0) in World Space
        const planeGeo = new THREE.PlaneGeometry(W_f, H_f);
        this.focalPlaneMesh = new THREE.Mesh(planeGeo, this.focalPlaneMat);
        this.focalPlaneMesh.position.set(0, 0, 0); // Focal plane at origin
        this.focalPlaneMesh.name = "FocalPlane"; // For raycasting
        this.group.add(this.focalPlaneMesh);
        
        // Add border to focal plane
        const edges = new THREE.EdgesGeometry(planeGeo);
        const border = new THREE.LineSegments(edges, this.focalPlaneBorderMat);
        this.focalPlaneMesh.add(border);

        // 3. Visualize Display Cube
        const cubeGeo = new THREE.BoxGeometry(W_f, H_f, d_cube);
        const cubeEdges = new THREE.EdgesGeometry(cubeGeo);
        this.displayCubeHelper = new THREE.LineSegments(cubeEdges, this.cubeMat);
        this.displayCubeHelper.position.set(0, 0, 0);
        this.group.add(this.displayCubeHelper);

        // 4. Create Cameras
        for (let i = 0; i < N; i++) {
            const theta = 2 * Math.atan((W_array / 2) / d_f);
            const u = (N > 1) ? i / (N - 1) : 0.5;
            const angle_i = (u - 0.5) * theta;
            // Removed negative sign to place Camera 0 at Left (-x) instead of Right (+x)
            const x_i = d_f * Math.tan(angle_i);
            
            const y_i = 0;
            const z_i = d_f; // Camera is at +d_f, looking at 0

            // Create Camera
            const camera = new THREE.PerspectiveCamera(
                THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(fovRad / 2) / aspect)), 
                aspect,
                near,
                far
            );
            
            // Manual Projection Matrix Calculation for Off-Axis
            const scale = near / d_f;
            const l = (-W_f / 2 - x_i) * scale;
            const r = (W_f / 2 - x_i) * scale;
            const t = (H_f / 2) * scale;
            const b = (-H_f / 2) * scale;
            
            const m = new THREE.Matrix4();
            const te = m.elements;
            const x = 2 * near / (r - l);
            const y = 2 * near / (t - b);
            const a = (r + l) / (r - l);
            const c = (t + b) / (t - b);
            const d = -(far + near) / (far - near);
            const e = -2 * far * near / (far - near);

            te[0] = x;  te[4] = 0;  te[8] = a;  te[12] = 0;
            te[1] = 0;  te[5] = y;  te[9] = c;  te[13] = 0;
            te[2] = 0;  te[6] = 0;  te[10] = d; te[14] = e;
            te[3] = 0;  te[7] = 0;  te[11] = -1; te[15] = 0;
            
            camera.projectionMatrix.copy(m);
            camera.projectionMatrixInverse.copy(m).invert();
            camera.updateProjectionMatrix = function() {}; 
            
            camera.position.set(x_i, y_i, z_i);
            camera.lookAt(x_i, y_i, 0); // Optical axis parallel to Z
            camera.updateMatrixWorld();
            
            this.cameras.push(camera);

            // Visualization
            const mesh = new THREE.Mesh(this.cameraBodyGeo, this.cameraBodyMat);
            mesh.position.copy(camera.position);
            this.group.add(mesh);
            this.cameraMeshes.push(mesh);

            if (showFrustums) {
                const helper = new THREE.CameraHelper(camera);
                if (i === activeCameraIndex) {
                    helper.setColors(new THREE.Color(0xcc6600), new THREE.Color(0xffaa00), new THREE.Color(0xcc6600), new THREE.Color(0xcc6600), new THREE.Color(0xcc6600)); // Dark Orange for active
                    mesh.material = new THREE.MeshStandardMaterial({ color: 0xffaa00, metalness: 0.6, roughness: 0.4 });
                    mesh.scale.setScalar(1.5);
                } else {
                     mesh.scale.setScalar(1.0);
                     mesh.material = this.cameraBodyMat;
                     // Set helper colors to something subtle but visible on white
                     // CameraHelper.setColors(frustum, cone, up, target, cross)
                     // Using a dark grey/navy for frustums
                     const c = new THREE.Color(0x888888); 
                     helper.setColors(c, c, c, c, c);
                }
                helper.visible = (showFrustums || i === activeCameraIndex);
                this.group.add(helper);
                this.helpers.push(helper);
            }
        }
    }
    
    // --- Texture Generation ---
    updateFocalPlaneTexture() {
        const { resW, resH } = this.lenticularParams;
        const activeCameraIndex = this.currentParams.activeCameraIndex || 0;
        
        // Create canvas
        // Reduce scaleFactor to 32 (Texture size 2048x2048) to ensure compatibility and avoid OOM
        // 2048 is safe for almost all devices (even mobile). 4096 might crash on some.
        const scaleFactor = 32; 
        const canvas = document.createElement('canvas');
        canvas.width = resW * scaleFactor;
        canvas.height = resH * scaleFactor;
        const ctx = canvas.getContext('2d');
        
        // Fill background Black (Screen style)
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        
        const subPixelW = scaleFactor / 3.0;
        
        // Smaller font size for sharpness and fit
        ctx.font = `bold ${scaleFactor * 0.3}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        
        // Draw pixels
        for (let y = 0; y < resH; y++) {
            for (let x = 0; x < resW; x++) {
                const px = x * scaleFactor;
                const py = y * scaleFactor;
                
                for (let k = 0; k < 3; k++) {
                    const viewId = this.getViewId(x, y, k);
                    
                    // Mode Logic
                    let isLit = false;
                    
                    if (this.focalPlaneMode === 'ViewMap') {
                        isLit = true;
                    } else {
                        // Lit Mode: Depends on Vis State
                        const { mode, channel } = this.visState;
                        
                        // 1. Check Channel
                        let channelMatch = false;
                        if (channel === 'All') channelMatch = true;
                        else if (channel === 'Red' && k === 0) channelMatch = true;
                        else if (channel === 'Green' && k === 1) channelMatch = true;
                        else if (channel === 'Blue' && k === 2) channelMatch = true;
                        
                        // 2. Check View
                        let viewMatch = false;
                        if (mode === 'All') {
                            viewMatch = true;
                        } else {
                            // Active (or None defaults to Active for texture)
                            viewMatch = (viewId === activeCameraIndex);
                        }
                        
                        isLit = channelMatch && viewMatch;
                    }

                    if (isLit) {
                        let color;
                        // Brighter subpixels for visibility on black
                        if (k === 0) color = '#ff4444'; 
                        else if (k === 1) color = '#44ff44';
                        else color = '#4444ff';
                        
                        ctx.fillStyle = color;
                        ctx.fillRect(px + k * subPixelW, py, subPixelW, scaleFactor);
                        
                        // Draw View ID (White text on black/color)
                        ctx.fillStyle = '#ffffff'; 
                        ctx.fillText(viewId, px + k * subPixelW + subPixelW/2, py + scaleFactor/2);
                    }
                }
                
                // Pixel Border - Dark Grey
                ctx.strokeStyle = '#333333';
                ctx.lineWidth = 2; // Thicker border for high res
                ctx.strokeRect(px, py, scaleFactor, scaleFactor);
            }
        }
        
        const texture = new THREE.CanvasTexture(canvas);
        // Use LinearFilter for minification to avoid shimmering artifacts on high-res texture
        // But NearestFilter might be better for pixel-perfect look if zoomed in.
        // User complained about "blurry" (could be linear filter) or "low res" (could be low scaleFactor).
        // With 4k texture, LinearFilter should look good and sharp.
        // Let's stick to Nearest for the "pixel art" aesthetic, but high res.
        texture.magFilter = THREE.LinearFilter; // Changed to Linear for better downsampling smoothness if needed, or keep Nearest for crispness?
        // User said "black screen". High res texture (4096) might be too big for some GPUs?
        // Or creation of texture failed.
        // Let's try a safer size if 4096 is the issue. 2048? 
        // 64 * 32 = 2048.
        // Let's revert scaleFactor to 32. 2048x2048 is universally supported. 4096 should be too, but maybe memory issue?
        // Or maybe context lost.
        
        texture.minFilter = THREE.LinearFilter; 
        texture.anisotropy = 16; 
        texture.colorSpace = THREE.SRGBColorSpace;
        
        // Update material
        if (this.focalPlaneMat.map) {
            this.focalPlaneMat.map.dispose();
        }
        this.focalPlaneMat.map = texture;
        this.focalPlaneMat.needsUpdate = true;
    }
    
    // --- Ray Visualization Methods ---
    
    // Convert Pixel (x, y) [0..63] to View ID using Lenticular Logic
    getViewId(x, y, k) {
        const { pe, tanAngle, offset, numViews } = this.lenticularParams;
        // D = 3*x + 3*y*tan + k + offset
        // Note: x, y here are integer pixel indices on the low-res grid (e.g. 64x64)
        const D = 3 * x + 3 * y * tanAngle + k + offset;
        let A = D % pe;
        if (A < 0) A += pe;
        const viewId = Math.floor(A / (pe / numViews)) % numViews;
        return viewId;
    }
    
    // Get world position of a subpixel on the focal plane
    getSubPixelPosition(x, y, k) {
        const { resW, resH } = this.lenticularParams;
        const W_f = this.W_f;
        const H_f = this.H_f;
        
        // Pixel size
        const pw = W_f / resW;
        const ph = H_f / resH;
        
        // Pixel Center (0,0 is Top-Left)
        // X: -W_f/2 + (x + 0.5)*pw
        // Y: +H_f/2 - (y + 0.5)*ph
        
        // Subpixel X offset: (k - 1) * (pw / 3) ? No, 0, 1, 2
        // Pixel x spans from x*pw to (x+1)*pw
        // Subpixel 0 center: x*pw + pw/6
        // Subpixel 1 center: x*pw + pw/2
        // Subpixel 2 center: x*pw + 5*pw/6
        
        const subPixelXOffset = (k + 0.5) * (pw / 3.0);
        
        const worldX = -W_f / 2 + x * pw + subPixelXOffset;
        const worldY = H_f / 2 - (y + 0.5) * ph;
        const worldZ = 0;
        
        return new THREE.Vector3(worldX, worldY, worldZ);
    }

    // Visualize a single pixel (3 subpixels -> 3 rays)
    highlightPixel(x, y) {
        this.rayGroup.clear();
        
        const positions = [];
        const colors = [];
        
        const subColors = [
            new THREE.Color(1, 0, 0), // R
            new THREE.Color(0, 1, 0), // G
            new THREE.Color(0, 0, 1)  // B
        ];
        
        for (let k = 0; k < 3; k++) {
            const viewId = this.getViewId(x, y, k);
            const targetPos = this.getSubPixelPosition(x, y, k);
            
            if (this.cameras[viewId]) {
                const camPos = this.cameras[viewId].position;
                
                positions.push(camPos.x, camPos.y, camPos.z);
                positions.push(targetPos.x, targetPos.y, targetPos.z);
                
                const c = subColors[k];
                colors.push(c.r, c.g, c.b);
                colors.push(c.r, c.g, c.b);
            }
        }
        
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
        
        const material = new THREE.LineBasicMaterial({ vertexColors: true, linewidth: 2 });
        const lines = new THREE.LineSegments(geometry, material);
        
        this.rayGroup.add(lines);
    }
    
    // Feature 2: Visualize all rays for a specific Camera (Viewpoint)
    showRaysForCamera(cameraIndex, channel = 'All') {
        this.rayGroup.clear();
        const { resW, resH } = this.lenticularParams;
        
        const positions = [];
        const colors = [];
        
        const subColors = [
            new THREE.Color(1, 0, 0),
            new THREE.Color(0, 1, 0),
            new THREE.Color(0, 0, 1)
        ];
        
        const cam = this.cameras[cameraIndex];
        if (!cam) return;
        const camPos = cam.position;

        for (let y = 0; y < resH; y++) {
            for (let x = 0; x < resW; x++) {
                for (let k = 0; k < 3; k++) {
                    // Channel Filter
                    if (channel === 'Red' && k !== 0) continue;
                    if (channel === 'Green' && k !== 1) continue;
                    if (channel === 'Blue' && k !== 2) continue;

                    const viewId = this.getViewId(x, y, k);
                    
                    if (viewId === cameraIndex) {
                        const targetPos = this.getSubPixelPosition(x, y, k);
                        
                        positions.push(camPos.x, camPos.y, camPos.z);
                        positions.push(targetPos.x, targetPos.y, targetPos.z);
                        
                        // Use dimmer colors for mass display
                        const c = subColors[k];
                        colors.push(c.r, c.g, c.b); // Start color (at camera)
                        colors.push(c.r * 0.5, c.g * 0.5, c.b * 0.5); // End color (at focal plane)
                    }
                }
            }
        }
        
        if (positions.length === 0) return;

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
        
        // Use transparent lines
        const material = new THREE.LineBasicMaterial({ 
            vertexColors: true, 
            transparent: true, 
            opacity: 0.6,
            blending: THREE.AdditiveBlending,
            linewidth: 2
        });
        const lines = new THREE.LineSegments(geometry, material);
        this.rayGroup.add(lines);
    }
    
    // Visualize ALL rays for 64x64 grid
    showAllRays(channel = 'All') {
        this.rayGroup.clear();
        const { resW, resH } = this.lenticularParams;
        
        const positions = [];
        const colors = [];
        
        const subColors = [
            new THREE.Color(1, 0, 0),
            new THREE.Color(0, 1, 0),
            new THREE.Color(0, 0, 1)
        ];
        
        // To avoid freezing, let's limit drawing or use simple lines
        // 64*64*3 = 12288 lines. WebGL can handle this easily.
        
        for (let y = 0; y < resH; y++) {
            for (let x = 0; x < resW; x++) {
                for (let k = 0; k < 3; k++) {
                    // Channel Filter
                    if (channel === 'Red' && k !== 0) continue;
                    if (channel === 'Green' && k !== 1) continue;
                    if (channel === 'Blue' && k !== 2) continue;

                    const viewId = this.getViewId(x, y, k);
                    
                    if (this.cameras[viewId]) {
                        const targetPos = this.getSubPixelPosition(x, y, k);
                        const camPos = this.cameras[viewId].position;
                        
                        positions.push(camPos.x, camPos.y, camPos.z);
                        positions.push(targetPos.x, targetPos.y, targetPos.z);
                        
                        // Use dimmer colors for mass display
                        const c = subColors[k];
                        colors.push(c.r * 0.3, c.g * 0.3, c.b * 0.3);
                        colors.push(c.r * 0.5, c.g * 0.5, c.b * 0.5); // Gradient to focal plane
                    }
                }
            }
        }
        
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
        
        // Use transparent lines
        const material = new THREE.LineBasicMaterial({ 
            vertexColors: true, 
            transparent: true, 
            opacity: 0.4,
            blending: THREE.AdditiveBlending,
            linewidth: 2
        });
        const lines = new THREE.LineSegments(geometry, material);
        this.rayGroup.add(lines);
    }
}

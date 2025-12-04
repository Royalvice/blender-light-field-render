import * as THREE from 'three';

export class LightFieldArray {
    constructor(scene) {
        this.scene = scene;
        this.group = new THREE.Group();
        this.scene.add(this.group);
        
        this.cameras = [];
        this.helpers = [];
        this.cameraMeshes = [];
        
        // Materials
        this.focalPlaneMat = new THREE.MeshBasicMaterial({ 
            color: 0x00ff00, 
            transparent: true, 
            opacity: 0.2, 
            side: THREE.DoubleSide,
            wireframe: false
        });
        this.focalPlaneBorderMat = new THREE.LineBasicMaterial({ color: 0x00ff00 });
        
        this.cubeMat = new THREE.LineBasicMaterial({ color: 0x0088ff, transparent: true, opacity: 0.5 });
        
        this.cameraBodyGeo = new THREE.BoxGeometry(0.1, 0.1, 0.2);
        this.cameraBodyMat = new THREE.MeshBasicMaterial({ color: 0xff0000 });

        // Objects
        this.focalPlaneMesh = null;
        this.displayCubeHelper = null;
    }

    update(params) {
        // Clean up existing
        this.group.clear();
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

        // 2. Visualize Focal Plane (at origin)
        // The focal plane is centered at (0,0,0) in World Space
        const planeGeo = new THREE.PlaneGeometry(W_f, H_f);
        this.focalPlaneMesh = new THREE.Mesh(planeGeo, this.focalPlaneMat);
        this.focalPlaneMesh.position.set(0, 0, 0); // Focal plane at origin
        this.group.add(this.focalPlaneMesh);
        
        // Add border to focal plane
        const edges = new THREE.EdgesGeometry(planeGeo);
        const border = new THREE.LineSegments(edges, this.focalPlaneBorderMat);
        this.focalPlaneMesh.add(border);

        // 3. Visualize Display Cube
        // Center at focal plane, depth D_cube
        const cubeGeo = new THREE.BoxGeometry(W_f, H_f, d_cube);
        const cubeEdges = new THREE.EdgesGeometry(cubeGeo);
        this.displayCubeHelper = new THREE.LineSegments(cubeEdges, this.cubeMat);
        this.displayCubeHelper.position.set(0, 0, 0);
        this.group.add(this.displayCubeHelper);

        // 4. Create Cameras
        // Cameras are located at Z = d_f (looking down -Z towards origin)
        // Positions distributed along X axis
        
        for (let i = 0; i < N; i++) {
            // Calculate x_i
            // From doc: x_i = -d_f * tan((i/(N-1) - 0.5) * theta)
            // theta (opening angle) = 2 * atan((W_array/2) / d_f)
            
            // Note: The doc formula for x_i implies the cameras are arranged on an arc?
            // Line 5: "视点沿着一条直线排列".
            // Line 41: x_i formula uses tan. This maps equi-angular spacing to linear position?
            // Usually linear array means x_i is linearly spaced: -W/2 to W/2.
            // Let's check the doc carefully.
            // "x_i = -d_f * tan((i/(N-1) - 0.5) * theta)"
            // If theta is the opening angle of the *array* viewed from the focal center?
            // Yes, if we want uniform angular sampling from the focal point, then x_i is non-linear.
            // If we want uniform linear sampling (standard linear array), x_i is linear.
            // The doc specifies this formula, so I will use it.
            
            const theta = 2 * Math.atan((W_array / 2) / d_f);
            const u = (N > 1) ? i / (N - 1) : 0.5;
            const angle_i = (u - 0.5) * theta;
            const x_i = -d_f * Math.tan(angle_i);
            
            const y_i = 0;
            const z_i = d_f; // Camera is at +d_f, looking at 0

            // Create Camera
            const camera = new THREE.PerspectiveCamera(
                THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(fovRad / 2) / aspect)), // Vertical FOV for constructor (approx)
                aspect,
                near,
                far
            );
            
            // Manual Projection Matrix Calculation for Off-Axis
            // We define the view volume at the Near Plane.
            // The view volume at the Focal Plane (Z = 0 in world, Z = -d_f in camera eye space) is [-W_f/2, W_f/2] x [-H_f/2, H_f/2] relative to the focal center.
            // Transform focal plane window corners to Camera Eye Space.
            // Camera is at (x_i, 0, d_f).
            // Point P_world = (X, Y, 0).
            // P_camera = P_world - C_pos = (X - x_i, Y, -d_f).
            
            // At Focal Plane distance (dist = d_f):
            // Left (at focal plane relative to camera axis) = -W_f/2 - x_i
            // Right = W_f/2 - x_i
            // Top = H_f/2
            // Bottom = -H_f/2
            
            // Project to Near Plane:
            const scale = near / d_f;
            const l = (-W_f / 2 - x_i) * scale;
            const r = (W_f / 2 - x_i) * scale;
            const t = (H_f / 2) * scale;
            const b = (-H_f / 2) * scale;
            
            // Force manual projection matrix
            // This ensures we bypass any internal Three.js updates that might reset it based on FOV/Aspect
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
            
            // Prevent auto-updates from overwriting our custom matrix
            camera.updateProjectionMatrix = function() {}; 
            
            // Position the camera object
            camera.position.set(x_i, y_i, z_i);
            camera.lookAt(x_i, y_i, 0); // Look parallel to Z axis?
            // Wait, "Parallel orientation".
            // If I lookAt(x_i, y_i, 0), the optical axis is (0, 0, -1). Correct.
            // The camera is at (x_i, 0, d_f) looking at (x_i, 0, 0). Correct.
            
            camera.updateMatrixWorld();
            
            this.cameras.push(camera);

            // Visualization
            
            // Camera Body
            const mesh = new THREE.Mesh(this.cameraBodyGeo, this.cameraBodyMat);
            mesh.position.copy(camera.position);
            this.group.add(mesh);
            this.cameraMeshes.push(mesh);

            // Frustum Helper
            if (showFrustums) {
                // Only show all if requested, or just the active one?
                // Let's show all faintly, or just active.
                // User setting passed in.
                const helper = new THREE.CameraHelper(camera);
                
                // Colors
                if (i === activeCameraIndex) {
                    helper.setColors(new THREE.Color(0xff0000), new THREE.Color(0xffaa00), new THREE.Color(0xff0000), new THREE.Color(0xff0000), new THREE.Color(0xff0000));
                    mesh.material = new THREE.MeshBasicMaterial({ color: 0xffff00 }); // Highlight active
                    mesh.scale.setScalar(1.5);
                } else {
                    // Dim others
                    // Helper colors are not easily transparent.
                    // We'll just add them if showFrustums is true, or if it's the active one.
                     mesh.scale.setScalar(1.0);
                     mesh.material = this.cameraBodyMat;
                }
                
                helper.visible = (showFrustums || i === activeCameraIndex);
                
                this.group.add(helper);
                this.helpers.push(helper);
            }
        }
    }
}


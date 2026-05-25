import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import GUI from 'lil-gui';
import { LightFieldArray } from './LightFieldArray.js';

// --- Scene Setup ---
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x111111);

// Lighting for Scientific Visualization
const ambientLight = new THREE.AmbientLight(0xffffff, 0.6); // Soft white light
scene.add(ambientLight);

const dirLight = new THREE.DirectionalLight(0xffffff, 0.8); // Key light
dirLight.position.set(10, 20, 10);
scene.add(dirLight);

const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
camera.position.set(10, 10, 20);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

// --- Helpers ---
const gridHelper = new THREE.GridHelper(20, 20, 0x444444, 0x222222);
scene.add(gridHelper);

const axesHelper = new THREE.AxesHelper(5);
scene.add(axesHelper);

// Add a simple object in the scene to visualize focus
// Replaced Torus with a static Cube with colored faces
const boxGeo = new THREE.BoxGeometry(2, 2, 2);
// 6 materials for 6 faces (Standard Material for better look)
const boxMaterials = [
    new THREE.MeshStandardMaterial({ color: 0xff4444 }), // Right (+x) - Red
    new THREE.MeshStandardMaterial({ color: 0x44ffff }), // Left (-x) - Cyan
    new THREE.MeshStandardMaterial({ color: 0x44ff44 }), // Top (+y) - Green
    new THREE.MeshStandardMaterial({ color: 0xff44ff }), // Bottom (-y) - Magenta
    new THREE.MeshStandardMaterial({ color: 0x4444ff }), // Front (+z) - Blue (Facing camera generally?)
    new THREE.MeshStandardMaterial({ color: 0xffff44 })  // Back (-z) - Yellow
];
// Note: Three.js BoxGeometry UV mapping default faces: +x, -x, +y, -y, +z, -z.
const coloredBox = new THREE.Mesh(boxGeo, boxMaterials);
// Position it slightly behind the focal plane? No, let's put it exactly centered at focal plane.
// If it's 2x2x2, it spans z from -1 to 1.
coloredBox.position.set(0, 0, 0);
scene.add(coloredBox);

// Add a red sphere at exact (0,0,0) to confirm focus center
const sphereGeo = new THREE.SphereGeometry(0.2, 32, 32);
const sphereMat = new THREE.MeshStandardMaterial({ color: 0xff0000 });
const sphere = new THREE.Mesh(sphereGeo, sphereMat);
scene.add(sphere);

// --- Light Field Array ---
const lfArray = new LightFieldArray(scene);

// --- Parameters ---
const params = {
    N: 60,
    d_f: 20,
    W_array: 10,
    fov_s: 45,
    aspect: 1.77, // 16:9
    near: 0.1,
    far: 100,
    d_cube: 3,
    showFrustums: true,
    activeCameraIndex: 0,
    
    // Array spread
    arrayAngleDeg: 0, // will be initialized below
    
    // View Control
    viewFromActive: false,
    
    // Focal Plane
    focalPlaneMode: 'ViewMap', // 'ViewMap' or 'Lit'
    
    // Movable probe plane
    showProbePlane: true,
    probePlaneZ: 0,
    probeScale: 1,
    probeHitSize: 0.08,
    
    // Info
    currentShiftX: 0
};

// Derive initial array spread angle from width/depth
params.arrayAngleDeg = THREE.MathUtils.radToDeg(2 * Math.atan((params.W_array / 2) / params.d_f));

// --- GUI ---
const gui = new GUI({ title: 'Light Field Settings' });

let activeCamController;
let widthController;
let angleController;

const updateArray = () => {
    // Keep width in sync with angle & depth so both controls stay consistent
    params.W_array = 2 * params.d_f * Math.tan(THREE.MathUtils.degToRad(params.arrayAngleDeg) / 2);
    if (widthController) widthController.updateDisplay();

    // Clamp active index
    if (params.activeCameraIndex >= params.N) {
        params.activeCameraIndex = params.N - 1;
    }
    
    // Sync Vis State for Texture
    let mode = 'Active';
    if (visParams.showAllRays) mode = 'All';
    lfArray.setRayVisualizationState(mode, visParams.channel);

    lfArray.setFocalPlaneMode(params.focalPlaneMode);
    lfArray.update(params);
    
    // Calculate Shift X for display
    if (lfArray.cameras[params.activeCameraIndex]) {
        const cam = lfArray.cameras[params.activeCameraIndex];
        const te = cam.projectionMatrix.elements;
        params.currentShiftX = te[8].toFixed(4);
    }

    // Update slider limit
    if (activeCamController) {
        activeCamController.max(params.N - 1);
        activeCamController.updateDisplay();
    }
    
    // Auto-update rays if active cam ray view is on
    if (visParams.showActiveCamRays) {
        lfArray.showRaysForCamera(params.activeCameraIndex, visParams.channel);
    } else if (visParams.showAllRays) {
        lfArray.showAllRays(visParams.channel);
    }
};

gui.add(params, 'N', 1, 100, 1).name('Camera Count').onChange(updateArray);
gui.add(params, 'd_f', 1, 50).name('Focal Dist (df)').onChange(() => {
    // Keep width consistent with angle when depth changes
    params.W_array = 2 * params.d_f * Math.tan(THREE.MathUtils.degToRad(params.arrayAngleDeg) / 2);
    if (widthController) widthController.updateDisplay();
    updateArray();
});
widthController = gui.add(params, 'W_array', 0.1, 20).name('Array Width').onChange(() => {
    // Changing width back-computes angle
    params.arrayAngleDeg = THREE.MathUtils.radToDeg(2 * Math.atan((params.W_array / 2) / params.d_f));
    if (angleController) angleController.updateDisplay();
    updateArray();
});
angleController = gui.add(params, 'arrayAngleDeg', 1, 170).name('Array Span Angle').onChange(() => {
    params.W_array = 2 * params.d_f * Math.tan(THREE.MathUtils.degToRad(params.arrayAngleDeg) / 2);
    if (widthController) widthController.updateDisplay();
    updateArray();
});
gui.add(params, 'fov_s', 10, 120).name('Horizontal FOV').onChange(updateArray);
gui.add(params, 'aspect', 0.1, 4).name('Aspect Ratio').onChange(updateArray);
gui.add(params, 'd_cube', 0.1, 20).name('Display Cube Depth').onChange(updateArray);
gui.add(params, 'showFrustums').name('Show All Frustums').onChange(updateArray);
gui.add(params, 'focalPlaneMode', ['ViewMap', 'Lit']).name('Focal Plane Mode').onChange(() => {
    lfArray.setFocalPlaneMode(params.focalPlaneMode);
});

const probeFolder = gui.addFolder('Probe Plane');
probeFolder.add(params, 'showProbePlane').name('Show Probe Plane').onChange(updateArray);
probeFolder.add(params, 'probePlaneZ', -40, 40).name('Probe Plane Z').onChange(updateArray);
probeFolder.add(params, 'probeScale', 0.1, 3).name('Probe Plane Scale').onChange(updateArray);
probeFolder.add(params, 'probeHitSize', 0.01, 0.5).name('Probe Hit Size').onChange(updateArray);

const camFolder = gui.addFolder('Active Camera');
activeCamController = camFolder.add(params, 'activeCameraIndex', 0, params.N - 1, 1).name('Index').onChange(() => {
    updateArray();
});

camFolder.add(params, 'viewFromActive').name('View Through Active');
camFolder.add(params, 'currentShiftX').name('Proj Matrix Shear X').listen().disable();

const visFolder = gui.addFolder('Ray Visualization');
const visParams = {
    showAllRays: false,
    showActiveCamRays: false,
    channel: 'All'
};

visFolder.add(visParams, 'channel', ['All', 'Red', 'Green', 'Blue']).name('RGB Channel').onChange(() => {
    // Sync state
    let mode = 'Active';
    if (visParams.showAllRays) mode = 'All';
    lfArray.setRayVisualizationState(mode, visParams.channel);

    // Force update of visualization
    if (visParams.showActiveCamRays) {
        lfArray.showRaysForCamera(params.activeCameraIndex, visParams.channel);
    } else if (visParams.showAllRays) {
        lfArray.showAllRays(visParams.channel);
    }
});

visFolder.add(visParams, 'showAllRays').name('Show All Rays (Grid)').onChange(v => {
    if (v) {
        visParams.showActiveCamRays = false; // Toggle others off
        // Sync state
        lfArray.setRayVisualizationState('All', visParams.channel);
        lfArray.showAllRays(visParams.channel);
    } else {
        lfArray.rayGroup.clear();
        // Revert to Active state for texture if rays are off? Or keep 'All' logic? 
        // User probably wants to see Active sparsity if nothing is shown.
        lfArray.setRayVisualizationState('Active', visParams.channel);
    }
    visFolder.controllers.forEach(c => c.updateDisplay());
});

visFolder.add(visParams, 'showActiveCamRays').name('Show Active Cam Rays').onChange(v => {
    if (v) {
        visParams.showAllRays = false;
        // Sync state
        lfArray.setRayVisualizationState('Active', visParams.channel);
        lfArray.showRaysForCamera(params.activeCameraIndex, visParams.channel);
    } else {
        lfArray.rayGroup.clear();
        lfArray.setRayVisualizationState('Active', visParams.channel);
    }
    visFolder.controllers.forEach(c => c.updateDisplay());
});

// Initial Build
updateArray();

// --- Raycasting ---
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();

window.addEventListener('mousemove', (event) => {
    // Calculate mouse position in normalized device coordinates
    // (-1 to +1) for both components
    mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;
    
    // Only perform raycasting if visualizations are OFF (to avoid clutter)
    // Or allow hover even if visualizaiton is on? Maybe confusing.
    // Let's allow hover only if no massive ray viz is active.
    
    if (!visParams.showAllRays && !visParams.showActiveCamRays && lfArray.focalPlaneMesh) {
        raycaster.setFromCamera(mouse, camera);
        
        const intersects = raycaster.intersectObject(lfArray.focalPlaneMesh);
        
        if (intersects.length > 0) {
            const hit = intersects[0];
            // Hit point in world space
            const p = hit.point;
            
            // Map World P to Pixel (x, y)
            const W_f = lfArray.W_f;
            const H_f = lfArray.H_f;
            const resW = 32;
            const resH = 32;
            
            // Transform world coordinate to UV [0, 1]
            // X: [-W/2, W/2] -> [0, 1]
            const u = (p.x + W_f / 2) / W_f;
            // Y: [H/2, -H/2] -> [0, 1]  (Top is 0, Bottom is 1 in pixel coords)
            const v = (H_f / 2 - p.y) / H_f;
            
            if (u >= 0 && u <= 1 && v >= 0 && v <= 1) {
                const px = Math.floor(u * resW);
                const py = Math.floor(v * resH);
                
                lfArray.highlightPixel(px, py);
            }
        } else {
            // Clear if not hovering focal plane
            lfArray.rayGroup.clear();
        }
    }
});


// --- Animation Loop ---
function animate() {
    requestAnimationFrame(animate);
    
    controls.update();
    
    if (params.viewFromActive && lfArray.cameras[params.activeCameraIndex]) {
        const activeCam = lfArray.cameras[params.activeCameraIndex];
        renderer.render(scene, activeCam);
    } else {
        renderer.render(scene, camera);
    }
}

animate();

// --- Resize ---
window.addEventListener('resize', () => {
    // Update main camera
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    
    // We do NOT update array cameras here, because their aspect is defined by 'params.aspect'
    // and their projection matrix is manually locked.
    // The renderer will stretch their image to fit the window.
    
    renderer.setSize(window.innerWidth, window.innerHeight);
});

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import GUI from 'lil-gui';
import { LightFieldArray } from './LightFieldArray.js';

// --- Scene Setup ---
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x111111);

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
// 6 materials for 6 faces
const boxMaterials = [
    new THREE.MeshBasicMaterial({ color: 0xff0000 }), // Right (+x) - Red
    new THREE.MeshBasicMaterial({ color: 0x00ffff }), // Left (-x) - Cyan
    new THREE.MeshBasicMaterial({ color: 0x00ff00 }), // Top (+y) - Green
    new THREE.MeshBasicMaterial({ color: 0xff00ff }), // Bottom (-y) - Magenta
    new THREE.MeshBasicMaterial({ color: 0x0000ff }), // Front (+z) - Blue (Facing camera generally?)
    new THREE.MeshBasicMaterial({ color: 0xffff00 })  // Back (-z) - Yellow
];
// Note: Three.js BoxGeometry UV mapping default faces: +x, -x, +y, -y, +z, -z.
const coloredBox = new THREE.Mesh(boxGeo, boxMaterials);
// Position it slightly behind the focal plane? No, let's put it exactly centered at focal plane.
// If it's 2x2x2, it spans z from -1 to 1.
coloredBox.position.set(0, 0, 0);
scene.add(coloredBox);

// Add a red sphere at exact (0,0,0) to confirm focus center
const sphereGeo = new THREE.SphereGeometry(0.2, 32, 32);
const sphereMat = new THREE.MeshBasicMaterial({ color: 0xff0000 });
const sphere = new THREE.Mesh(sphereGeo, sphereMat);
scene.add(sphere);

// --- Light Field Array ---
const lfArray = new LightFieldArray(scene);

// --- Parameters ---
const params = {
    N: 10,
    d_f: 10,
    W_array: 5,
    fov_s: 45,
    aspect: 1.77, // 16:9
    near: 0.1,
    far: 50,
    d_cube: 3,
    showFrustums: true,
    activeCameraIndex: 0,
    
    // View Control
    viewFromActive: false,
    
    // Info
    currentShiftX: 0
};

// --- GUI ---
const gui = new GUI({ title: 'Light Field Settings' });

let activeCamController;

const updateArray = () => {
    // Clamp active index
    if (params.activeCameraIndex >= params.N) {
        params.activeCameraIndex = params.N - 1;
    }
    
    lfArray.update(params);
    
    // Calculate Shift X for display
    // s_i = x_i / (d_f * tan(fov/2) * aspect) ??
    // Let's use the doc formula:
    // s_i = x_i / (d_f * tan(FOV_s/2) * r_s)
    // My derivation: s (shear X) = -x_i / (d_f * tan(fov_x/2))
    // Note: Matrix element [0][2] is s_x.
    // Let's read the actual matrix element from the camera.
    if (lfArray.cameras[params.activeCameraIndex]) {
        const cam = lfArray.cameras[params.activeCameraIndex];
        const te = cam.projectionMatrix.elements;
        // Matrix is column-major. 
        // P = [ 0 4 8 12
        //       1 5 9 13
        //       ... ]
        // element 8 is (0, 2).
        params.currentShiftX = te[8].toFixed(4);
    }

    // Update slider limit
    if (activeCamController) {
        activeCamController.max(params.N - 1);
        activeCamController.updateDisplay();
    }
};

gui.add(params, 'N', 1, 100, 1).name('Camera Count').onChange(updateArray);
gui.add(params, 'd_f', 1, 50).name('Focal Dist (df)').onChange(updateArray);
gui.add(params, 'W_array', 0.1, 20).name('Array Width').onChange(updateArray);
gui.add(params, 'fov_s', 10, 120).name('Horizontal FOV').onChange(updateArray);
gui.add(params, 'aspect', 0.1, 4).name('Aspect Ratio').onChange(updateArray);
gui.add(params, 'd_cube', 0.1, 20).name('Display Cube Depth').onChange(updateArray);
gui.add(params, 'showFrustums').name('Show All Frustums').onChange(updateArray);

const camFolder = gui.addFolder('Active Camera');
activeCamController = camFolder.add(params, 'activeCameraIndex', 0, params.N - 1, 1).name('Index').onChange(() => {
    updateArray();
});

camFolder.add(params, 'viewFromActive').name('View Through Active');
camFolder.add(params, 'currentShiftX').name('Proj Matrix Shear X').listen().disable();

// Initial Build
updateArray();

// --- Animation Loop ---
function animate() {
    requestAnimationFrame(animate);
    
    controls.update();
    
    // torus.rotation.x += 0.01;
    // torus.rotation.y += 0.01;
    
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


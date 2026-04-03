import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { fromArrayBuffer } from 'geotiff'; // Importation stricte pour Vite

let scene, camera, renderer, controls;
let terrainMesh = null;
let baseAltitudes = [];
let minZ = 0;

// Outils de dessin
let isDrawing = false;
let jumpPoints = [];
let markers = [];
let drawingGroup = new THREE.Group();
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();

function init() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x2c3e50);
    scene.add(drawingGroup);

    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 5000);
    camera.position.set(0, 300, 300);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    document.body.appendChild(renderer.domElement);

    controls = new OrbitControls(camera, renderer.domElement);

    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const dirLight = new THREE.DirectionalLight(0xffffff, 1);
    dirLight.position.set(100, 200, 100);
    scene.add(dirLight);

    window.addEventListener('click', onMouseClick, false);
    window.addEventListener('resize', onWindowResize, false);
    
    animate();
}

// 1. LECTURE DU TIF
document.getElementById('mnt-upload').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    try {
        const arrayBuffer = await file.arrayBuffer();
        const tiff = await fromArrayBuffer(arrayBuffer);
        const image = await tiff.getImage();
        const width = image.getWidth();
        const height = image.getHeight();
        const rasters = await image.readRasters();
        
        baseAltitudes = Array.from(rasters[0]);
        creerTerrain(baseAltitudes, width, height);
    } catch (error) {
        console.error("Erreur de lecture TIF :", error);
        alert("Erreur lors de la lecture du fichier TIF.");
    }
});

// 2. CRÉATION DU TERRAIN 3D (Échelle 1 point = 0.5m)
function creerTerrain(altitudes, width, height) {
    if (terrainMesh) scene.remove(terrainMesh);

    const validAlts = altitudes.filter(v => v > -5000 && v < 9000);
    minZ = Math.min(...validAlts);

    const resolutionXY = 0.5; 
    const geometry = new THREE.PlaneGeometry((width - 1) * resolutionXY, (height - 1) * resolutionXY, width - 1, height - 1);
    const positions = geometry.attributes.position;

    for (let i = 0; i < positions.count; i++) {
        let z = altitudes[i];
        if (z < -5000 || z > 9000 || isNaN(z)) z = minZ;
        positions.setZ(i, z - minZ);
    }
    
    geometry.computeVertexNormals();

    const material = new THREE.MeshStandardMaterial({ color: 0xdddddd, flatShading: true, side: THREE.DoubleSide });
    terrainMesh = new THREE.Mesh(geometry, material);
    terrainMesh.rotation.x = -Math.PI / 2;
    scene.add(terrainMesh);
    controls.target.set(0, 0, 0);
}

// 3. NEIGE DE CULTURE
document.getElementById('snow-slider').addEventListener('input', (e) => {
    if (!terrainMesh) return;
    const h = parseFloat(e.target.value);
    document.getElementById('snow-val').innerText = h;
    
    const pos = terrainMesh.geometry.attributes.position;
    for (let i = 0; i < pos.count; i++) {
        let z = baseAltitudes[i];
        if (z < -5000 || z > 9000 || isNaN(z)) z = minZ;
        pos.setZ(i, (z - minZ) + h);
    }
    terrainMesh.geometry.computeVertexNormals();
    pos.needsUpdate = true;
});

// 4. OUTIL DE DESSIN ET NORMES
document.getElementById('btn-draw').addEventListener('click', () => {
    if(!terrainMesh) return alert("Chargez un MNT d'abord.");
    isDrawing = true;
    document.getElementById('btn-draw').style.background = "#ffcc00";
    document.getElementById('btn-draw').innerText = "Cliquez 4 points...";
});

document.getElementById('btn-clear').addEventListener('click', () => {
    isDrawing = false;
    jumpPoints = [];
    markers.forEach(m => scene.remove(m));
    markers = [];
    drawingGroup.clear();
    document.getElementById('norm-panel').style.display = "none";
    document.getElementById('btn-draw').style.background = "#4da6ff";
    document.getElementById('btn-draw').innerText = "✏️ Tracer un Saut (4 clics)";
});

function onMouseClick(event) {
    if (!isDrawing || !terrainMesh || jumpPoints.length >= 4) return;
    
    mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);

    const intersects = raycaster.intersectObject(terrainMesh);
    if (intersects.length > 0) {
        const point = intersects[0].point;
        jumpPoints.push(point);

        const sphere = new THREE.Mesh(new THREE.SphereGeometry(1.5, 16, 16), new THREE.MeshBasicMaterial({ color: jumpPoints.length === 2 ? 0xff0000 : 0x0000ff }));
        sphere.position.copy(point);
        scene.add(sphere);
        markers.push(sphere);

        if (jumpPoints.length > 1) {
            const geo = new THREE.BufferGeometry().setFromPoints([jumpPoints[jumpPoints.length - 2], point]);
            drawingGroup.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0x000000, linewidth: 3 })));
        }

        if (jumpPoints.length === 4) {
            analyserSaut();
            isDrawing = false;
            document.getElementById('btn-draw').style.background = "#4da6ff";
            document.getElementById('btn-draw').innerText = "✏️ Nouveau Saut";
        }
    }
}

// 5. CALCUL BALISTIQUE
function analyserSaut() {
    const [p0, p1, p2, p3] = jumpPoints;
    const deniveleInRun = p0.y - p1.y;
    let vitesseSortie = 0;
    
    if (deniveleInRun > 0) vitesseSortie = Math.sqrt(2 * 9.81 * deniveleInRun) * 0.85; // 0.85 = friction neige

    const angleKicker = 35 * (Math.PI / 180);
    const dirVect = new THREE.Vector3().subVectors(p1, p0);
    dirVect.y = 0; dirVect.normalize();

    const pointsParabole = [];
    let impactPoint = null;
    let t = 0;

    while (t < 6) {
        const x = p1.x + dirVect.x * (vitesseSortie * Math.cos(angleKicker)) * t;
        const z = p1.z + dirVect.z * (vitesseSortie * Math.cos(angleKicker)) * t;
        const y = p1.y + (vitesseSortie * Math.sin(angleKicker)) * t - (0.5 * 9.81 * t * t);
        
        const currentPos = new THREE.Vector3(x, y, z);
        pointsParabole.push(currentPos);

        if (t > 0.1 && y <= p2.y) { impactPoint = currentPos; break; }
        t += 0.05;
    }

    const parGeo = new THREE.BufferGeometry().setFromPoints(pointsParabole);
    const line = new THREE.Line(parGeo, new THREE.LineDashedMaterial({ color: 0xff0000, dashSize: 2, gapSize: 2 }));
    line.computeLineDistances();
    drawingGroup.add(line);

    document.getElementById('norm-panel').style.display = "block";
    document.getElementById('stat-vitesse').innerText = (vitesseSortie * 3.6).toFixed(1);
    document.getElementById('stat-vol').innerText = (impactPoint ? p1.distanceTo(impactPoint) : 0).toFixed(1);

    const statusBadge = document.getElementById('stat-status');
    const flatDist = new THREE.Vector2(p1.x, p1.z).distanceTo(new THREE.Vector2(p2.x, p2.z));
    const impactDist2D = new THREE.Vector2(p1.x, p1.z).distanceTo(new THREE.Vector2(impactPoint ? impactPoint.x : p3.x, impactPoint ? impactPoint.z : p3.z));

    if (impactDist2D < flatDist) {
        statusBadge.className = "status-badge status-red";
        statusBadge.innerText = "❌ DANGER : Réception sur le plat !";
    } else {
        statusBadge.className = "status-badge status-green";
        statusBadge.innerText = "✅ SÉCURISÉ : Réception dans la pente.";
    }
}

function onWindowResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
}

function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
}

init();

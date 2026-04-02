import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
// L'importation la plus stable pour Vite.js :
import * as GeoTIFF from 'geotiff';

// --- VARIABLES GLOBALES ---
let scene, camera, renderer, controls;
let layers = []; 
let activeLayerIndex = -1;

// Variables pour le dessin
let isDrawing = false;
let jumpPoints = [];
let markers = [];
let drawingGroup = new THREE.Group();
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();

// --- 1. INITIALISATION ---
function init() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x2c3e50);
    scene.add(drawingGroup);

    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 10000);
    // On recule beaucoup la caméra car à l'échelle 1:1, le terrain peut être très grand !
    camera.position.set(0, 500, 500); 

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    document.body.appendChild(renderer.domElement);

    controls = new OrbitControls(camera, renderer.domElement);

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambientLight);
    
    const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
    dirLight.position.set(500, 1000, 500);
    scene.add(dirLight);

    window.addEventListener('click', onMouseClick, false);
    animate();
}

// --- 2. LECTURE DES TIF ---
document.getElementById('mnt-upload').addEventListener('change', async function(e) {
    const files = e.target.files;
    if(files.length === 0) return;

    for (let file of files) {
        try {
            const arrayBuffer = await file.arrayBuffer();
            await parserTIF(arrayBuffer, file.name);
        } catch (err) {
            alert("Erreur critique avec le fichier " + file.name + ". Regardez la console (F12).");
            console.error(err);
        }
    }
    // On réinitialise l'input pour pouvoir recharger le même fichier si besoin
    e.target.value = ''; 
});

async function parserTIF(arrayBuffer, fileName) {
    try {
        console.log(`Décodage de ${fileName}...`);
        // Utilisation de la méthode robuste de l'import complet
        const tiff = await GeoTIFF.fromArrayBuffer(arrayBuffer);
        const image = await tiff.getImage();
        const width = image.getWidth();
        const height = image.getHeight();
        const rasters = await image.readRasters();
        
        const altitudes = Array.from(rasters[0]);
        console.log(`Fichier lu : ${width}x${height} points.`);

        creerNouvelleCouche(altitudes, width, height, fileName);
    } catch (error) {
        console.error("Erreur de parsing TIF:", error);
        alert(`Le fichier ${fileName} n'est pas un TIF valide ou est corrompu.`);
    }
}

// --- 3. GÉNÉRATION 3D (ÉCHELLE 1:1 / 0.5m) ---
function creerNouvelleCouche(altitudes, width, height, name) {
    // 1. Filtrer les NoData (Trous de scan)
    const altitudesValides = altitudes.filter(v => v > -5000 && v < 9000);
    if (altitudesValides.length === 0) {
        alert("Ce fichier TIF ne contient aucune donnée d'altitude lisible.");
        return;
    }
    const minZ = Math.min(...altitudesValides);

    // 2. Grille à l'échelle (0.5m par point)
    const resolutionXY = 0.5; 
    const widthMeters = (width - 1) * resolutionXY;
    const heightMeters = (height - 1) * resolutionXY;

    // Si le fichier est monstrueux (plus de 1000x1000), on prévient
    if (width > 1500 || height > 1500) {
        alert("Attention, ce MNT est très lourd. Le rendu 3D peut être lent.");
    }

    const geometry = new THREE.PlaneGeometry(widthMeters, heightMeters, width - 1, height - 1);
    const positions = geometry.attributes.position;

    // 3. Altimétrie exacte
    for (let i = 0; i < positions.count; i++) {
        let z = altitudes[i];
        if (z < -5000 || z > 9000 || isNaN(z)) z = minZ; 
        
        // On ancre la base de la montagne à Y=0 dans la scène, au centimètre près
        positions.setZ(i, z - minZ); 
    }
    geometry.computeVertexNormals();

    const hue = Math.random();
    const material = new THREE.MeshStandardMaterial({ 
        color: new THREE.Color().setHSL(hue, 0.4, 0.8), // Couleur aléatoire pastel
        side: THREE.DoubleSide,
        flatShading: true 
    });

    const mesh = new THREE.Mesh(geometry, material);
    mesh.rotation.x = -Math.PI / 2; // Coucher la carte
    scene.add(mesh);

    // Centrer la caméra sur le nouveau terrain
    controls.target.set(0, 0, 0);

    layers.push({ id: Date.now(), name, mesh, baseAltitudes: altitudes, visible: true, minZ });
    activeLayerIndex = layers.length - 1;
    refreshLayersUI();
}

// --- 4. INTERFACE DES COUCHES ---
function refreshLayersUI() {
    const list = document.getElementById('layers-list');
    list.innerHTML = layers.length === 0 ? '<p style="color:#888;">Aucun TIF chargé</p>' : '';
    
    layers.forEach((layer, i) => {
        const item = document.createElement('div');
        item.className = `layer-item ${i === activeLayerIndex ? 'active' : ''}`;
        item.innerHTML = `<span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:140px;" title="${layer.name}">${layer.name}</span>
            <div class="layer-controls">
                <button onclick="window.toggleLayer(${i})">👁️</button>
                <button onclick="window.deleteLayer(${i})">🗑️</button>
            </div>`;
        item.onclick = (e) => { 
            if(e.target.tagName !== 'BUTTON') { 
                activeLayerIndex = i; 
                document.getElementById('snow-slider').value = 0;
                document.getElementById('snow-val').innerText = "0";
                refreshLayersUI(); 
            }
        };
        list.appendChild(item);
    });
}
window.toggleLayer = (i) => { layers[i].visible = !layers[i].visible; layers[i].mesh.visible = layers[i].visible; refreshLayersUI(); };
window.deleteLayer = (i) => { scene.remove(layers[i].mesh); layers[i].mesh.geometry.dispose(); layers[i].mesh.material.dispose(); layers.splice(i, 1); activeLayerIndex = -1; refreshLayersUI(); };

// --- 5. NEIGE DE CULTURE ---
document.getElementById('snow-slider').addEventListener('input', (e) => {
    if (activeLayerIndex === -1) return;
    const h = parseFloat(e.target.value);
    document.getElementById('snow-val').innerText = h;
    const l = layers[activeLayerIndex];
    const pos = l.mesh.geometry.attributes.position;
    for (let i = 0; i < pos.count; i++) {
        let z = l.baseAltitudes[i];
        if (z < -5000 || z > 9000 || isNaN(z)) z = l.minZ;
        pos.setZ(i, (z - l.minZ) + h);
    }
    l.mesh.geometry.computeVertexNormals();
    pos.needsUpdate = true;
});

// --- 6. OUTILS DE DESSIN ---
document.getElementById('btn-draw').addEventListener('click', () => {
    if(activeLayerIndex === -1) return alert("Chargez un MNT d'abord.");
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
    if (!isDrawing || activeLayerIndex === -1 || jumpPoints.length >= 4) return;
    
    mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);

    const intersects = raycaster.intersectObject(layers[activeLayerIndex].mesh);
    if (intersects.length > 0) {
        const point = intersects[0].point;
        jumpPoints.push(point);

        const geometry = new THREE.SphereGeometry(2, 16, 16);
        const material = new THREE.MeshBasicMaterial({ color: jumpPoints.length === 2 ? 0xff0000 : 0x0000ff });
        const sphere = new THREE.Mesh(geometry, material);
        sphere.position.copy(point);
        scene.add(sphere);
        markers.push(sphere);

        if (jumpPoints.length > 1) {
            const mat = new THREE.LineBasicMaterial({ color: 0x000000, linewidth: 3 });
            const geo = new THREE.BufferGeometry().setFromPoints([jumpPoints[jumpPoints.length - 2], point]);
            drawingGroup.add(new THREE.Line(geo, mat));
        }

        if (jumpPoints.length === 4) {
            analyserSaut();
            isDrawing = false;
            document.getElementById('btn-draw').style.background = "#4da6ff";
            document.getElementById('btn-draw').innerText = "✏️ Nouveau Saut";
        }
    }
}

function analyserSaut() {
    const p0 = jumpPoints[0], p1 = jumpPoints[1], p2 = jumpPoints[2], p3 = jumpPoints[3];
    const deniveleInRun = p0.y - p1.y;
    let vitesseSortie = 0;
    
    if (deniveleInRun > 0) vitesseSortie = Math.sqrt(2 * 9.81 * deniveleInRun) * 0.8;

    const angleKicker = 35 * (Math.PI / 180);
    const dirVect = new THREE.Vector3().subVectors(p1, p0);
    dirVect.y = 0; dirVect.normalize();

    const pointsParabole = [];
    let impactPoint = null;
    let t = 0;

    while (t < 5) {
        const x = p1.x + dirVect.x * (vitesseSortie * Math.cos(angleKicker)) * t;
        const z = p1.z + dirVect.z * (vitesseSortie * Math.cos(angleKicker)) * t;
        const y = p1.y + (vitesseSortie * Math.sin(angleKicker)) * t - (0.5 * 9.81 * t * t);
        
        const currentPos = new THREE.Vector3(x, y, z);
        pointsParabole.push(currentPos);

        if (t > 0.1 && y <= p2.y) { impactPoint = currentPos; break; }
        t += 0.05;
    }

    const parGeo = new THREE.BufferGeometry().setFromPoints(pointsParabole);
    const parMat = new THREE.LineDashedMaterial({ color: 0xff0000, dashSize: 2, gapSize: 2 });
    const line = new THREE.Line(parGeo, parMat);
    line.computeLineDistances();
    drawingGroup.add(line);

    document.getElementById('norm-panel').style.display = "block";
    document.getElementById('stat-vitesse').innerText = (vitesseSortie * 3.6).toFixed(1);
    
    const volDist = impactPoint ? p1.distanceTo(impactPoint) : 0;
    document.getElementById('stat-vol').innerText = volDist.toFixed(1);

    const statusBadge = document.getElementById('stat-status');
    const flatDist = new THREE.Vector2(p1.x, p1.z).distanceTo(new THREE.Vector2(p2.x, p2.z));
    const impactDist2D = new THREE.Vector2(p1.x, p1.z).distanceTo(new THREE.Vector2(impactPoint ? impactPoint.x : p3.x, impactPoint ? impactPoint.z : p3.z));

    if (impactDist2D < flatDist) {
        statusBadge.className = "status-badge status-red";
        statusBadge.innerText = "DANGER : Réception sur le plat !";
    } else {
        statusBadge.className = "status-badge status-green";
        statusBadge.innerText = "SÉCURISÉ : Réception OK.";
    }
}

// --- BOUCLE ---
function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
}
window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});
init();

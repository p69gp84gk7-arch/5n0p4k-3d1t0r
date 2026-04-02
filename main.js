import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { fromArrayBuffer } from 'geotiff'; // Import corrigé pour Vite.js

// --- VARIABLES GLOBALES ---
let scene, camera, renderer, controls;
let layers = []; 
let activeLayerIndex = -1;

// Variables pour le dessin
let isDrawing = false;
let jumpPoints = []; // Stocke les 4 points du saut
let markers = []; // Les sphères visuelles
let drawingGroup = new THREE.Group(); // Contient les lignes et paraboles
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();

// --- 1. INITIALISATION ---
function init() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x2c3e50);
    scene.add(drawingGroup);

    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 5000);
    camera.position.set(0, 100, 200);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    document.body.appendChild(renderer.domElement);

    controls = new OrbitControls(camera, renderer.domElement);

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);
    
    const dirLight = new THREE.DirectionalLight(0xffffff, 1);
    dirLight.position.set(100, 200, 50);
    scene.add(dirLight);

    // Écouteur pour le Raycasting (Clic sur le terrain)
    window.addEventListener('click', onMouseClick, false);

    animate();
}

// --- 2. LECTURE DES TIF ---
document.getElementById('mnt-upload').addEventListener('change', async function(e) {
    const files = e.target.files;
    for (let file of files) {
        const arrayBuffer = await file.arrayBuffer();
        await parserTIF(arrayBuffer, file.name);
    }
});

async function parserTIF(arrayBuffer, fileName) {
    try {
        const tiff = await fromArrayBuffer(arrayBuffer);
        const image = await tiff.getImage();
        const width = image.getWidth();
        const height = image.getHeight();
        const rasters = await image.readRasters();
        const altitudes = Array.from(rasters[0]);
        creerNouvelleCouche(altitudes, width, height, fileName);
    } catch (error) {
        console.error(error);
        alert(`Erreur avec ${fileName}. Format non reconnu.`);
    }
}

// --- 3. GÉNÉRATION 3D (ÉCHELLE RÉELLE ET PRÉCISION ABSOLUE) ---
function creerNouvelleCouche(altitudes, width, height, name) {
    // 1. Trouver l'altitude minimale réelle (pour mettre la base du terrain à Z=0 visuellement)
    const altitudesValides = altitudes.filter(v => v > -5000 && v < 9000);
    if (altitudesValides.length === 0) {
        alert("Ce fichier ne semble pas contenir de données d'altitude.");
        return;
    }
    const minZ = Math.min(...altitudesValides);

    // 2. RÉSOLUTION SPATIALE X/Y (0.5 mètre par point)
    const resolutionXY = 0.5; 
    const widthMeters = (width - 1) * resolutionXY;
    const heightMeters = (height - 1) * resolutionXY;

    console.log(`📏 Taille réelle du terrain : ${widthMeters}m x ${heightMeters}m`);
    console.log(`🎯 Résolution du maillage : 1 point tous les ${resolutionXY}m`);

    // On crée le plan avec les dimensions exactes en mètres
    // (width - 1) et (height - 1) garantissent qu'on a exactement un sommet 3D pour chaque point de ton TIF
    const geometry = new THREE.PlaneGeometry(widthMeters, heightMeters, width - 1, height - 1);
    const positions = geometry.attributes.position;

    // 3. ALTIMÉTRIE EXACTE (Z)
    for (let i = 0; i < positions.count; i++) {
        let z = altitudes[i];

        // Nettoyage des valeurs aberrantes (trous dans le scan)
        if (z < -5000 || z > 9000 || isNaN(z)) {
            z = minZ; 
        }

        // ICI : 1 unité 3D = 1 mètre réel. 
        // L'altimétrie est conservée avec sa précision d'origine (décimale / centimètre)
        positions.setZ(i, z - minZ); 
    }
    
    // Recalcul des normales pour que la lumière accroche les moindres détails du relief
    geometry.computeVertexNormals();

    const material = new THREE.MeshStandardMaterial({ 
        color: 0xdddddd, 
        side: THREE.DoubleSide,
        flatShading: true,
        wireframe: false // Tu peux le passer à "true" pour vérifier visuellement que tes carrés font 0.5m
    });

    const mesh = new THREE.Mesh(geometry, material);
    mesh.rotation.x = -Math.PI / 2;
    scene.add(mesh);

    layers.push({ 
        id: Date.now(), 
        name, 
        mesh, 
        baseAltitudes: altitudes, 
        visible: true, 
        minZ 
    });
    
    activeLayerIndex = layers.length - 1;
    refreshLayersUI();
    
    console.log("✅ Couche 3D à l'échelle générée !");
}

// --- 4. GESTION DES COUCHES ET NEIGE ---
function refreshLayersUI() {
    const list = document.getElementById('layers-list');
    list.innerHTML = layers.length === 0 ? '<p>Aucun TIF chargé</p>' : '';
    layers.forEach((layer, i) => {
        const item = document.createElement('div');
        item.className = `layer-item ${i === activeLayerIndex ? 'active' : ''}`;
        item.innerHTML = `<span>${layer.name.substring(0, 12)}...</span>
            <div class="layer-controls">
                <button onclick="window.toggleLayer(${i})">👁️</button>
                <button onclick="window.deleteLayer(${i})">🗑️</button>
            </div>`;
        item.onclick = (e) => { 
            if(e.target.tagName !== 'BUTTON') { activeLayerIndex = i; refreshLayersUI(); }
        };
        list.appendChild(item);
    });
}
window.toggleLayer = (i) => { layers[i].visible = !layers[i].visible; layers[i].mesh.visible = layers[i].visible; };
window.deleteLayer = (i) => { scene.remove(layers[i].mesh); layers.splice(i, 1); activeLayerIndex = -1; refreshLayersUI(); };

document.getElementById('snow-slider').addEventListener('input', (e) => {
    if (activeLayerIndex === -1) return;
    const h = parseFloat(e.target.value);
    document.getElementById('snow-val').innerText = h;
    const l = layers[activeLayerIndex];
    const pos = l.mesh.geometry.attributes.position;
    for (let i = 0; i < pos.count; i++) {
        const zBase = l.baseAltitudes[i] > -9000 ? (l.baseAltitudes[i] - l.minZ) * 0.1 : 0;
        pos.setZ(i, zBase + h);
    }
    l.mesh.geometry.computeVertexNormals();
    pos.needsUpdate = true;
});

// --- 5. OUTILS DE DESSIN ET NORMES (NOUVEAU) ---
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
    
    // Convertir clic souris en coordonnées 3D
    mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);

    const intersects = raycaster.intersectObject(layers[activeLayerIndex].mesh);
    if (intersects.length > 0) {
        const point = intersects[0].point;
        jumpPoints.push(point);

        // Ajouter un marqueur visuel
        const geometry = new THREE.SphereGeometry(1.5, 16, 16);
        const material = new THREE.MeshBasicMaterial({ color: jumpPoints.length === 2 ? 0xff0000 : 0x0000ff });
        const sphere = new THREE.Mesh(geometry, material);
        sphere.position.copy(point);
        scene.add(sphere);
        markers.push(sphere);

        // Dessiner ligne
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

// Moteur Physique Simplifié
function analyserSaut() {
    const p0 = jumpPoints[0]; // Départ
    const p1 = jumpPoints[1]; // Kicker
    const p2 = jumpPoints[2]; // Fin plat
    const p3 = jumpPoints[3]; // Fin landing

    // 1. Calcul Vitesse (Chute libre avec friction sur rampe)
    const deniveleInRun = p0.y - p1.y;
    let vitesseSortie = 0;
    if (deniveleInRun > 0) {
        vitesseSortie = Math.sqrt(2 * 9.81 * deniveleInRun) * 0.8; // -20% pour friction neige
    }

    // 2. Trajectoire Parabolique
    const angleKicker = 35 * (Math.PI / 180); // Angle standard de sortie 35°
    const dirVect = new THREE.Vector3().subVectors(p1, p0);
    dirVect.y = 0; dirVect.normalize(); // Vecteur de direction 2D

    const pointsParabole = [];
    let impactPoint = null;
    let t = 0;

    // Simulation balistique
    while (t < 5) { // max 5 secondes de vol
        const x = p1.x + dirVect.x * (vitesseSortie * Math.cos(angleKicker)) * t;
        const z = p1.z + dirVect.z * (vitesseSortie * Math.cos(angleKicker)) * t;
        const y = p1.y + (vitesseSortie * Math.sin(angleKicker)) * t - (0.5 * 9.81 * t * t);
        
        const currentPos = new THREE.Vector3(x, y, z);
        pointsParabole.push(currentPos);

        // Simple détection de collision avec le "flat" (P1 -> P2)
        const distFromKicker = new THREE.Vector2(x, z).distanceTo(new THREE.Vector2(p1.x, p1.z));
        const platLength = new THREE.Vector2(p2.x, p2.z).distanceTo(new THREE.Vector2(p1.x, p1.z));

        // Si la parabole descend plus bas que la hauteur du terrain, c'est l'impact
        if (t > 0.1 && y <= p2.y) {
            impactPoint = currentPos;
            break;
        }
        t += 0.05;
    }

    // Dessiner la parabole
    const parGeo = new THREE.BufferGeometry().setFromPoints(pointsParabole);
    const parMat = new THREE.LineDashedMaterial({ color: 0xff0000, dashSize: 1, gapSize: 1 });
    const line = new THREE.Line(parGeo, parMat);
    line.computeLineDistances();
    drawingGroup.add(line);

    // Analyse Sécurité Normes
    document.getElementById('norm-panel').style.display = "block";
    document.getElementById('stat-vitesse').innerText = (vitesseSortie * 3.6).toFixed(1); // m/s en km/h
    
    let volDist = 0;
    if (impactPoint) volDist = p1.distanceTo(impactPoint);
    document.getElementById('stat-vol').innerText = volDist.toFixed(1);

    const statusBadge = document.getElementById('stat-status');
    const flatDist = new THREE.Vector2(p1.x, p1.z).distanceTo(new THREE.Vector2(p2.x, p2.z));
    const impactDist2D = new THREE.Vector2(p1.x, p1.z).distanceTo(new THREE.Vector2(impactPoint ? impactPoint.x : p3.x, impactPoint ? impactPoint.z : p3.z));

    if (impactDist2D < flatDist) {
        statusBadge.className = "status-badge status-red";
        statusBadge.innerText = "DANGER : Réception sur le plat !";
    } else {
        statusBadge.className = "status-badge status-green";
        statusBadge.innerText = "SÉCURISÉ : Réception dans la pente.";
    }
}

// --- BOUCLE D'ANIMATION ---
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

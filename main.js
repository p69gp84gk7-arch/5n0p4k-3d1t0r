import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import * as GeoTIFF from 'geotiff';

// --- VARIABLES GLOBALES ---
let scene, camera, renderer, controls;
let layers = []; // TABLEAU DE COUCHES : { id, name, mesh, baseAltitudes }
let activeLayerIndex = -1;

// --- INITIALISATION (inchangée) ---
function init() {
    // ... (ton code init habituel)
    animate();
}

// --- LECTURE DU FICHIER MNT (.TIF) ---
document.getElementById('mnt-upload').addEventListener('change', async function(e) {
    const files = e.target.files;
    for (let file of files) {
        const arrayBuffer = await file.arrayBuffer();
        await parserTIF(arrayBuffer, file.name);
    }
});

async function parserTIF(arrayBuffer, fileName) {
    const tiff = await GeoTIFF.fromArrayBuffer(arrayBuffer);
    const image = await tiff.getImage();
    const width = image.getWidth();
    const height = image.getHeight();
    const rasters = await image.readRasters();
    const altitudes = Array.from(rasters[0]);

    creerNouvelleCouche(altitudes, width, height, fileName);
}

// --- CRÉATION ET GESTION DES COUCHES ---
function creerNouvelleCouche(altitudes, width, height, name) {
    const geometry = new THREE.PlaneGeometry(100, 100, width - 1, height - 1);
    const positions = geometry.attributes.position;
    const minZ = Math.min(...altitudes.filter(v => v > -9999)); // Gère les valeurs "no data"

    for (let i = 0; i < positions.count; i++) {
        const z = altitudes[i] > -9999 ? (altitudes[i] - minZ) * 0.1 : 0;
        positions.setZ(i, z);
    }
    geometry.computeVertexNormals();

    // Couleur aléatoire pour différencier les couches
    const randomColor = new THREE.Color(Math.random(), Math.random(), Math.random());
    const material = new THREE.MeshStandardMaterial({ 
        color: randomColor, 
        transparent: true, 
        opacity: 0.8,
        side: THREE.DoubleSide 
    });

    const mesh = new THREE.Mesh(geometry, material);
    mesh.rotation.x = -Math.PI / 2;
    scene.add(mesh);

    const newLayer = {
        id: Date.now(),
        name: name,
        mesh: mesh,
        baseAltitudes: altitudes,
        visible: true
    };

    layers.push(newLayer);
    activeLayerIndex = layers.length - 1;
    refreshLayersUI();
}

// --- MISE À JOUR DE L'INTERFACE LISTE ---
function refreshLayersUI() {
    const listContainer = document.getElementById('layers-list');
    listContainer.innerHTML = "";

    if (layers.length === 0) {
        listContainer.innerHTML = '<p style="font-size: 0.8rem; color: #888;">Aucun MNT chargé</p>';
        return;
    }

    layers.forEach((layer, index) => {
        const item = document.createElement('div');
        item.className = `layer-item ${index === activeLayerIndex ? 'active' : ''}`;
        item.innerHTML = `
            <span>${layer.name.substring(0, 15)}...</span>
            <div class="layer-controls">
                <button onclick="window.toggleLayer(${index})">${layer.visible ? '👁️' : '🕶️'}</button>
                <button onclick="window.deleteLayer(${index})">🗑️</button>
            </div>
        `;
        item.onclick = () => { activeLayerIndex = index; refreshLayersUI(); };
        listContainer.appendChild(item);
    });
}

// --- FONCTIONS PILOTES (Exposées globalement) ---
window.toggleLayer = (index) => {
    layers[index].visible = !layers[index].visible;
    layers[index].mesh.visible = layers[index].visible;
    refreshLayersUI();
};

window.deleteLayer = (index) => {
    // Supprime de la scène 3D
    scene.remove(layers[index].mesh);
    layers[index].mesh.geometry.dispose();
    layers[index].mesh.material.dispose();
    
    // Supprime du tableau
    layers.splice(index, 1);
    activeLayerIndex = layers.length > 0 ? 0 : -1;
    refreshLayersUI();
};

// --- GESTION DE LA NEIGE SUR LA COUCHE ACTIVE ---
document.getElementById('snow-slider').addEventListener('input', function(e) {
    if (activeLayerIndex === -1) return;
    
    const hauteur = parseFloat(e.target.value);
    const layer = layers[activeLayerIndex];
    const positions = layer.mesh.geometry.attributes.position;
    const minZ = Math.min(...layer.baseAltitudes.filter(v => v > -9999));

    for (let i = 0; i < positions.count; i++) {
        const zBase = (layer.baseAltitudes[i] - minZ) * 0.1;
        positions.setZ(i, zBase + hauteur);
    }
    layer.mesh.geometry.attributes.position.needsUpdate = true;
});

function animate() {
    requestAnimationFrame(animate);
    if(controls) controls.update();
    renderer.render(scene, camera);
}

init();

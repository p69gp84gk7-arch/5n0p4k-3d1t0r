import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import * as GeoTIFF from 'geotiff'; // Lecteur spécifique pour tes fichiers .tif

// --- VARIABLES GLOBALES ---
let scene, camera, renderer, controls;
let layers = []; 
let activeLayerIndex = -1;

// --- 1. INITIALISATION DE LA SCÈNE 3D ---
function init() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x2c3e50); // Fond sombre élégant

    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 2000);
    camera.position.set(0, 80, 150);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    document.body.appendChild(renderer.domElement);

    controls = new OrbitControls(camera, renderer.domElement);

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
    scene.add(ambientLight);
    
    const dirLight = new THREE.DirectionalLight(0xffffff, 1);
    dirLight.position.set(50, 100, 50);
    scene.add(dirLight);

    animate();
}

// --- 2. LECTURE DES FICHIERS .TIF ---
document.getElementById('mnt-upload').addEventListener('change', async function(e) {
    const files = e.target.files;
    if (files.length === 0) return;

    // Boucle pour traiter chaque fichier TIF sélectionné
    for (let file of files) {
        const arrayBuffer = await file.arrayBuffer();
        await parserTIF(arrayBuffer, file.name);
    }
});

async function parserTIF(arrayBuffer, fileName) {
    try {
        // La magie de geotiff.js opère ici
        const tiff = await GeoTIFF.fromArrayBuffer(arrayBuffer);
        const image = await tiff.getImage();
        const width = image.getWidth();
        const height = image.getHeight();
        const rasters = await image.readRasters();
        const altitudes = Array.from(rasters[0]);

        creerNouvelleCouche(altitudes, width, height, fileName);
    } catch (error) {
        console.error("Erreur avec le fichier TIF :", error);
        alert(`Impossible de lire le fichier ${fileName}. Assure-toi que c'est un GeoTIFF valide.`);
    }
}

// --- 3. CRÉATION DE LA COUCHE 3D ---
function creerNouvelleCouche(altitudes, width, height, name) {
    const geometry = new THREE.PlaneGeometry(100, 100, width - 1, height - 1);
    const positions = geometry.attributes.position;
    
    // Ignorer les valeurs "NoData" (souvent très négatives dans les TIF) pour trouver le vrai point le plus bas
    const minZ = Math.min(...altitudes.filter(v => v > -9000));

    for (let i = 0; i < positions.count; i++) {
        // Ajustement de l'altitude (le facteur 0.1 gère l'exagération du relief)
        const z = altitudes[i] > -9000 ? (altitudes[i] - minZ) * 0.1 : 0;
        positions.setZ(i, z);
    }
    geometry.computeVertexNormals();

    // On attribue une couleur légèrement différente à chaque nouvelle couche
    const hue = Math.random();
    const material = new THREE.MeshStandardMaterial({ 
        color: new THREE.Color().setHSL(hue, 0.5, 0.8), 
        transparent: true, 
        opacity: 0.9,
        side: THREE.DoubleSide 
    });

    const mesh = new THREE.Mesh(geometry, material);
    mesh.rotation.x = -Math.PI / 2;
    scene.add(mesh);

    // Ajout à notre base de données locale des couches
    const newLayer = {
        id: Date.now(),
        name: name,
        mesh: mesh,
        baseAltitudes: altitudes,
        visible: true
    };

    layers.push(newLayer);
    activeLayerIndex = layers.length - 1; // Sélectionne automatiquement la nouvelle couche
    refreshLayersUI();
}

// --- 4. GESTION DE L'INTERFACE DES COUCHES ---
function refreshLayersUI() {
    const listContainer = document.getElementById('layers-list');
    listContainer.innerHTML = "";

    if (layers.length === 0) {
        listContainer.innerHTML = '<p style="font-size: 0.8rem; color: #888;">Aucun TIF chargé</p>';
        return;
    }

    layers.forEach((layer, index) => {
        const item = document.createElement('div');
        item.className = `layer-item ${index === activeLayerIndex ? 'active' : ''}`;
        
        // Structure HTML d'une ligne de couche
        item.innerHTML = `
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 130px;" title="${layer.name}">
                ${layer.name}
            </span>
            <div class="layer-controls">
                <button onclick="window.toggleLayer(${index})" title="Masquer/Afficher">${layer.visible ? '👁️' : '🕶️'}</button>
                <button onclick="window.deleteLayer(${index})" title="Supprimer">🗑️</button>
            </div>
        `;
        
        // Rendre la ligne cliquable pour la sélectionner
        item.onclick = (e) => { 
            if(e.target.tagName !== 'BUTTON') {
                activeLayerIndex = index; 
                document.getElementById('snow-slider').value = 0; // Reset le slider de neige visuel
                document.getElementById('snow-val').innerText = "0";
                refreshLayersUI(); 
            }
        };
        listContainer.appendChild(item);
    });
}

// Fonctions globales pour les boutons HTML
window.toggleLayer = (index) => {
    layers[index].visible = !layers[index].visible;
    layers[index].mesh.visible = layers[index].visible;
    refreshLayersUI();
};

window.deleteLayer = (index) => {
    scene.remove(layers[index].mesh);
    layers[index].mesh.geometry.dispose();
    layers[index].mesh.material.dispose();
    
    layers.splice(index, 1);
    activeLayerIndex = layers.length > 0 ? 0 : -1;
    refreshLayersUI();
};

// --- 5. SIMULATION DE LA NEIGE ---
document.getElementById('snow-slider').addEventListener('input', function(e) {
    if (activeLayerIndex === -1) return; // Sécurité si aucune couche sélectionnée
    
    const hauteurNeige = parseFloat(e.target.value);
    document.getElementById('snow-val').innerText = hauteurNeige;

    const layer = layers[activeLayerIndex];
    const positions = layer.mesh.geometry.attributes.position;
    const minZ = Math.min(...layer.baseAltitudes.filter(v => v > -9000));

    for (let i = 0; i < positions.count; i++) {
        const zBase = layer.baseAltitudes[i] > -9000 ? (layer.baseAltitudes[i] - minZ) * 0.1 : 0;
        // On applique la neige uniquement sur la couche active
        positions.setZ(i, zBase + hauteurNeige);
    }
    
    layer.mesh.geometry.computeVertexNormals();
    layer.mesh.geometry.attributes.position.needsUpdate = true;
});

// --- BOUCLE D'ANIMATION ---
function animate() {
    requestAnimationFrame(animate);
    if(controls) controls.update();
    renderer.render(scene, camera);
}

// Adaptation à la taille de l'écran
window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});

init();

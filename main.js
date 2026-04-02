import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import Chart from 'chart.js/auto';

// --- CONFIGURATION APP SCRIPT ---
// Remplacer par l'URL fournie par Google Apps Script lors du déploiement
const GOOGLE_SHEET_URL = "TON_URL_APPS_SCRIPT_ICI";

// --- VARIABLES GLOBALES ---
let scene, camera, renderer, controls;
let terrainMesh;
let baseAltitudes = []; // Stocke le terrain brut sans neige
let profileChart;

// --- 1. INITIALISATION DE LA SCÈNE 3D ---
function init() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x87CEEB); // Ciel bleu

    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
    camera.position.set(0, 50, 100);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    document.body.appendChild(renderer.domElement);

    controls = new OrbitControls(camera, renderer.domElement);

    // Lumières
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);
    
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(50, 100, 50);
    scene.add(dirLight);

    initChart();
    animate();
}

// --- 2. LECTURE DU FICHIER MNT ---
document.getElementById('mnt-upload').addEventListener('change', function(e) {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = function(event) {
        const contenu = event.target.result;
        parserMNT(contenu);
    };
    reader.readAsText(file);
});

// Parseur simplifié (suppose un fichier avec une liste de hauteurs ou un CSV)
function parserMNT(texte) {
    // Nettoie le texte et extrait tous les nombres
    const valeurs = texte.trim().split(/[\s,]+/).map(Number).filter(n => !isNaN(n));
    
    if(valeurs.length === 0) {
        alert("Fichier non reconnu ou vide.");
        return;
    }

    // On déduit la taille de la grille (doit être un carré parfait idéalement)
    const segments = Math.floor(Math.sqrt(valeurs.length)) - 1;
    genererTerrain(valeurs, segments);
}

// --- 3. GÉNÉRATION DU TERRAIN ---
function genererTerrain(altitudes, segments) {
    if (terrainMesh) scene.remove(terrainMesh); // Supprime l'ancien terrain
    
    baseAltitudes = altitudes; // On sauvegarde l'état naturel

    const geometry = new THREE.PlaneGeometry(100, 100, segments, segments);
    const positions = geometry.attributes.position;

    for (let i = 0; i < positions.count; i++) {
        // Applique l'altitude (ajustée visuellement si besoin avec un multiplicateur)
        positions.setZ(i, baseAltitudes[i] || 0); 
    }

    geometry.computeVertexNormals();

    const material = new THREE.MeshStandardMaterial({ 
        color: 0xffffff, // Blanc neige
        roughness: 0.8,
        wireframe: false 
    });

    terrainMesh = new THREE.Mesh(geometry, material);
    terrainMesh.rotation.x = -Math.PI / 2; // Coucher la montagne
    scene.add(terrainMesh);

    updateProfileChart(); // Met à jour le graphique 2D
}

// --- 4. GESTION DE LA NEIGE DE CULTURE ---
document.getElementById('snow-slider').addEventListener('input', function(e) {
    const hauteurNeige = parseFloat(e.target.value);
    document.getElementById('snow-val').innerText = hauteurNeige;

    if(!terrainMesh) return;

    const positions = terrainMesh.geometry.attributes.position;
    
    for (let i = 0; i < positions.count; i++) {
        // Z actuel = Altitude naturelle + Neige
        positions.setZ(i, baseAltitudes[i] + hauteurNeige);
    }

    terrainMesh.geometry.computeVertexNormals();
    terrainMesh.geometry.attributes.position.needsUpdate = true;
    
    updateProfileChart(hauteurNeige);
});

// --- 5. COUPE DE PROFIL (Chart.js) ---
function initChart() {
    const ctx = document.getElementById('profileChart').getContext('2d');
    profileChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Profil du Terrain',
                data: [],
                borderColor: '#4da6ff',
                backgroundColor: 'rgba(77, 166, 255, 0.2)',
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { title: { display: true, text: 'Altitude (m)' } } }
        }
    });
}

function updateProfileChart(hauteurNeige = 0) {
    if(baseAltitudes.length === 0) return;

    // On prend une "tranche" du terrain (le milieu) pour l'exemple
    const taille = Math.floor(Math.sqrt(baseAltitudes.length));
    const coupe = [];
    const labels = [];
    
    const ligneMilieu = Math.floor(taille / 2);
    
    for(let i = 0; i < taille; i++) {
        const index = ligneMilieu * taille + i;
        coupe.push(baseAltitudes[index] + hauteurNeige);
        labels.push(i + "m");
    }

    profileChart.data.labels = labels;
    profileChart.data.datasets[0].data = coupe;
    profileChart.update();
}

// --- 6. SAUVEGARDE GOOGLE SHEETS ---
document.getElementById('save-btn').addEventListener('click', () => {
    if(baseAltitudes.length === 0) {
        alert("Veuillez d'abord charger un terrain !");
        return;
    }

    const bouton = document.getElementById('save-btn');
    bouton.innerText = "Sauvegarde en cours...";

    const donnees = {
        nom: "Snowpark Alpha",
        neige: document.getElementById('snow-slider').value,
        fichierInfos: `${baseAltitudes.length} points topographiques`
    };

    fetch(GOOGLE_SHEET_URL, {
        method: 'POST',
        body: JSON.stringify(donnees)
    })
    .then(response => response.json())
    .then(data => {
        alert("Projet sauvegardé sur Google Sheets !");
        bouton.innerText = "💾 Sauvegarder sur Cloud";
    })
    .catch(error => {
        console.error("Erreur:", error);
        alert("Erreur lors de la sauvegarde.");
        bouton.innerText = "💾 Sauvegarder sur Cloud";
    });
});

// --- BOUCLE D'ANIMATION ---
function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
}

// Lancer l'application
window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});

init();

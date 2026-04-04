// On attend que la page soit bien chargée
document.addEventListener('DOMContentLoaded', () => {
    
    // 1. INITIALISATION DE LA CARTE
    const map = new maplibregl.Map({
        container: 'map-container', // L'endroit où la carte s'affiche
        // On utilise un style de carte topographique gratuit (OpenStreetMap)
        style: {
            'version': 8,
            'sources': {
                'osm-topo': {
                    'type': 'raster',
                    'tiles': [
                        'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
                    ],
                    'tileSize': 256,
                    'attribution': 'Map data: © OpenStreetMap contributors, SRTM | Map style: © OpenTopoMap'
                }
            },
            'layers': [
                {
                    'id': 'osm-topo-layer',
                    'type': 'raster',
                    'source': 'osm-topo'
                }
            ]
        },
        center: [0.5936, 42.7610], // Longitude, Latitude (Superbagnères)
        zoom: 13, // Niveau de zoom initial
        pitch: 45 // Inclinaison pour donner un léger effet de perspective
    });

    // Ajout des boutons de contrôle (+ / - / boussole)
    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    console.log("Carte chargée avec succès !");
});

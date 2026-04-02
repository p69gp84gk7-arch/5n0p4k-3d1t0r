// On utilise l'importation déstructurée, souvent plus stable avec Vite
import { fromArrayBuffer } from 'geotiff';

// Si ce message ne s'affiche pas dans la console (F12), c'est que le script ne charge pas du tout !
console.log("🚀 ÉTAPE 1 : Le script main.js est bien lancé !");

const uploadInput = document.getElementById('mnt-upload');

if (!uploadInput) {
    console.error("❌ ERREUR : Le bouton d'upload n'a pas été trouvé dans le HTML !");
} else {
    console.log("✅ ÉTAPE 2 : Le bouton d'upload est prêt.");
    
    uploadInput.addEventListener('change', async function(e) {
        console.log("📂 ÉTAPE 3 : Fichier sélectionné !");
        
        const file = e.target.files[0];
        if (!file) return;

        try {
            console.log(`⏳ Lecture de ${file.name}...`);
            const arrayBuffer = await file.arrayBuffer();
            
            // Tentative de décodage du TIF
            const tiff = await fromArrayBuffer(arrayBuffer);
            const image = await tiff.getImage();
            
            console.log(`🎉 SUCCÈS ! Le TIF est lisible. Dimensions : ${image.getWidth()} x ${image.getHeight()}`);
            alert("✅ Le fichier TIF fonctionne parfaitement ! On peut remettre la 3D.");
            
        } catch (error) {
            console.error("❌ ERREUR DE DÉCODAGE TIF :", error);
            alert("Erreur lors de la lecture du fichier. Regarde la console (F12).");
        }
    });
}

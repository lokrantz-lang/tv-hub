plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "se.tvhub.box"
    compileSdk = 35

    defaultConfig {
        applicationId = "se.tvhub.box"
        // Formuler Z Alpha kör Android 7.0 (API 24). Z10 Pro kör Android 10.
        minSdk = 24
        targetSdk = 34
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("VERSION_NAME") ?: "1.0.0"
    }

    signingConfigs {
        create("release") {
            // Sätts av GitHub Actions från hemligheter. Saknas de signeras med debug-nyckeln.
            val keystorePath = System.getenv("TVHUB_KEYSTORE")
            if (keystorePath != null && file(keystorePath).exists()) {
                storeFile = file(keystorePath)
                storePassword = System.getenv("TVHUB_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("TVHUB_KEY_ALIAS") ?: "tvhub"
                keyPassword = System.getenv("TVHUB_KEY_PASSWORD") ?: System.getenv("TVHUB_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            val release = signingConfigs.getByName("release")
            signingConfig = if (release.storeFile != null) release else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = true
    }

    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

dependencies {
    val media3 = "1.4.1"
    implementation("androidx.core:core:1.13.1")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.annotation:annotation:1.8.2")
    implementation("androidx.annotation:annotation-experimental:1.4.1")
    implementation("androidx.media3:media3-exoplayer:$media3")
    implementation("androidx.media3:media3-exoplayer-hls:$media3")
    implementation("androidx.media3:media3-extractor:$media3")
    implementation("androidx.media3:media3-ui:$media3")
    // Chromecast från telefonen. Utan Google Play-tjänster (vissa boxar) gör de ingenting.
    implementation("com.google.android.gms:play-services-cast-framework:21.4.0")
    implementation("androidx.mediarouter:mediarouter:1.6.0")
}

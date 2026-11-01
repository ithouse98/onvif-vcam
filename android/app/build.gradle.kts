plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.ipsco.vcamagent"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.ipsco.vcamagent"
        minSdk = 26
        targetSdk = 34
        versionCode = 140
        versionName = "1.4.0"
    }

    signingConfigs {
        create("release") {
            // public test key (keystore/README.md); set VCAM_KEYSTORE* to sign with your own
            storeFile = file(System.getenv("VCAM_KEYSTORE") ?: "../keystore/vcam-agent.jks")
            storePassword = System.getenv("VCAM_KEYSTORE_PASSWORD") ?: "vcamagent"
            keyAlias = System.getenv("VCAM_KEY_ALIAS") ?: "vcamagent"
            keyPassword = System.getenv("VCAM_KEY_PASSWORD") ?: "vcamagent"
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    val camerax = "1.3.4"
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.camera:camera-core:$camerax")
    implementation("androidx.camera:camera-camera2:$camerax")
    implementation("androidx.camera:camera-lifecycle:$camerax")
    implementation("androidx.lifecycle:lifecycle-service:2.8.4")
}

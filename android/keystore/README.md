# Signing key

`vcam-agent.jks` is a **public test key** (password `vcamagent`, alias `vcamagent`) so that every
APK built from this repository installs over the previous one. Anyone can sign with it, so for a
production deployment sign with your own key:

    keytool -genkeypair -keystore my.jks -alias myalias -keyalg RSA -keysize 2048 -validity 10000
    VCAM_KEYSTORE=/path/my.jks VCAM_KEYSTORE_PASSWORD=... VCAM_KEY_ALIAS=myalias VCAM_KEY_PASSWORD=... \
        gradle assembleRelease

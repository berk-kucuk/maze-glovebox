"""English and Turkish strings.

English is the default whatever the system locale. Turkish is one click away
in the title bar (remembered in settings.json) or `maze-glovebox --lang=tr`.
`t()` falls back to English, then to the key.
"""
from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "bug_body": "This was a bug, not something you did. Nothing was changed; "
                    "the details are in the system journal.",
        "tip_minimize": "Minimize",
        "tip_maximize": "Maximize / Restore",
        "tip_close": "Close",
        "tip_language": "Language",
        # hero
        "eyebrow": "SAFE OPENING",
        "hero_title": "Open the file.\nKeep it at arm's length.",
        "hero_sub": "An attachment you did not expect, a download you are not sure of: "
                    "Glovebox opens a copy of it in a sealed box. The app inside can show "
                    "you the file — and cannot reach the internet, your files or your "
                    "other apps.",
        "btn_choose": "Choose a file",
        "chamber_empty": "Drop a file into the box",
        "chamber_or": "or",
        "rail_inside": "INSIDE",
        "rail_limits": "LIMITS",
        "more_pending": "{n} more waiting",
        "drop_hint": "or drop a file anywhere in this window",
        # sealed list
        "sec_sealed": "INSIDE THE BOX, THE APP HAS",
        "sealed_net": "No network",
        "sealed_net_d": "Not the internet, not your local network, not even this computer.",
        "sealed_home": "None of your files",
        "sealed_home_d": "Its home holds only a copy of this one file. Your documents, "
                         "keys and browser profiles are not there.",
        "sealed_apps": "No way to your other apps",
        "sealed_apps_d": "No D-Bus, no sound, no access to other windows or what you type.",
        "sealed_trace": "Nothing left behind",
        "sealed_trace_d": "The copy, and anything the app writes, is in memory and deleted "
                          "when it closes. The original is never changed.",
        # pending file
        "sec_open": "OPEN",
        "open_with": "Open with",
        "btn_open": "Open in Glovebox",
        "btn_remove": "Remove",
        "no_handler": "No app on this computer opens this kind of file in a way that "
                      "works inside the box.",
        "reason_sandboxed_package": "Flatpak/Snap — cannot run inside the box",
        "reason_outside_system": "installed outside /usr — cannot run inside the box",
        "reason_missing": "program not found",
        "reason_unreadable": "broken menu entry",
        # running
        "sec_running": "OPEN IN GLOVEBOX",
        "running_empty": "Nothing is open in the box.",
        "running_row": "{file} — in {app}",
        "mode_x11": "separate X11 window",
        "mode_wayland": "Wayland",
        "since": "since {time}",
        "btn_close_box": "Close",
        # limits
        "sec_limits": "WHAT THE BOX CANNOT DO",
        "limit_view": "It protects your computer from the file, not you from what the "
                      "file says: a fake invoice or a scam is still a scam.",
        "limit_clipboard": "What you copy inside the box can be pasted outside it — "
                           "do not paste links or commands from a suspicious file.",
        "limit_save": "Changes made inside the box are thrown away. To keep an edited "
                      "copy, do not edit a file you do not trust.",
        "limit_kernel": "A box shares the Linux kernel with your system. It stops "
                        "documents and their viewers, not an exploit of the kernel itself.",
        # status / errors
        "status_ready": "{n} open in the box",
        "status_idle": "Choose a file to begin",
        "e_no_wayland": "Glovebox needs a Wayland session (Plasma's default) to show a window.",
        "e_no_bwrap": "bubblewrap is not installed (sudo pacman -S bubblewrap).",
        "e_too_big": "This file is too big to copy into the box's memory.",
        "e_unreadable": "The file could not be read.",
        "e_unusable_app": "That app cannot run inside the box.",
        # notifications (also used by the command line)
        "n_opened": "{file} opened in {app} inside Glovebox — no network, no access to "
                    "your files.",
        "n_failed": "{app} could not open the file inside Glovebox: {detail}",
        "n_no_handler": "No app that works inside Glovebox opens {file}. Choose one in "
                        "the Glovebox window.",
        "n_error": "Glovebox: {detail}",
    },
    "tr": {
        "bug_body": "Bu bir hataydı, sizin yaptığınız bir şey değil. Hiçbir şey "
                    "değiştirilmedi; ayrıntılar sistem günlüğünde.",
        "tip_minimize": "Küçült",
        "tip_maximize": "Büyüt / Geri al",
        "tip_close": "Kapat",
        "tip_language": "Dil",
        "eyebrow": "GÜVENLİ AÇMA",
        "hero_title": "Dosyayı açın.\nAma size dokunamasın.",
        "hero_sub": "Beklemediğiniz bir ek, emin olmadığınız bir indirme: Glovebox "
                    "dosyanın bir kopyasını kapalı bir kutuda açar. İçerideki uygulama "
                    "dosyayı size gösterebilir — ama internete, dosyalarınıza ve diğer "
                    "uygulamalarınıza ulaşamaz.",
        "btn_choose": "Dosya seç",
        "chamber_empty": "Bir dosyayı kutuya bırakın",
        "chamber_or": "ya da",
        "rail_inside": "İÇERİDE",
        "rail_limits": "SINIRLAR",
        "more_pending": "{n} dosya daha bekliyor",
        "drop_hint": "ya da bir dosyayı bu pencerenin herhangi bir yerine bırakın",
        "sec_sealed": "KUTUNUN İÇİNDE UYGULAMANIN",
        "sealed_net": "Ağı yok",
        "sealed_net_d": "İnternet yok, yerel ağınız yok, bu bilgisayarın kendisi bile yok.",
        "sealed_home": "Dosyalarınıza erişimi yok",
        "sealed_home_d": "Ev klasöründe yalnızca bu tek dosyanın bir kopyası var. "
                         "Belgeleriniz, anahtarlarınız ve tarayıcı profilleriniz orada değil.",
        "sealed_apps": "Diğer uygulamalarınıza yolu yok",
        "sealed_apps_d": "D-Bus yok, ses yok; diğer pencerelere ve yazdıklarınıza erişim yok.",
        "sealed_trace": "Geride iz yok",
        "sealed_trace_d": "Kopya ve uygulamanın yazdığı her şey bellekte durur ve kapanınca "
                          "silinir. Orijinal hiç değişmez.",
        "sec_open": "AÇ",
        "open_with": "Şununla aç",
        "btn_open": "Glovebox'ta aç",
        "btn_remove": "Kaldır",
        "no_handler": "Bu bilgisayarda bu tür dosyayı kutunun içinde açabilecek bir "
                      "uygulama yok.",
        "reason_sandboxed_package": "Flatpak/Snap — kutunun içinde çalışamaz",
        "reason_outside_system": "/usr dışına kurulu — kutunun içinde çalışamaz",
        "reason_missing": "program bulunamadı",
        "reason_unreadable": "bozuk menü girdisi",
        "sec_running": "GLOVEBOX'TA AÇIK",
        "running_empty": "Kutuda açık bir şey yok.",
        "running_row": "{file} — {app} ile",
        "mode_x11": "ayrı X11 penceresi",
        "mode_wayland": "Wayland",
        "since": "açılış: {time}",
        "btn_close_box": "Kapat",
        "sec_limits": "KUTUNUN YAPAMADIKLARI",
        "limit_view": "Bilgisayarınızı dosyadan korur, sizi dosyanın söylediğinden "
                      "korumaz: sahte bir fatura ya da dolandırıcılık yine öyledir.",
        "limit_clipboard": "Kutunun içinde kopyaladığınız şey dışarıya yapıştırılabilir — "
                           "şüpheli bir dosyadan bağlantı ya da komut yapıştırmayın.",
        "limit_save": "Kutunun içinde yapılan değişiklikler atılır. Güvenmediğiniz bir "
                      "dosyayı düzenlemeyin.",
        "limit_kernel": "Kutu, Linux çekirdeğini sisteminizle paylaşır. Belgeleri ve "
                        "onları açan uygulamaları durdurur; çekirdeğin kendisine yönelik "
                        "bir saldırıyı değil.",
        "status_ready": "Kutuda {n} açık",
        "status_idle": "Başlamak için bir dosya seçin",
        "e_no_wayland": "Glovebox'ın pencere gösterebilmesi için bir Wayland oturumu "
                        "(Plasma'nın varsayılanı) gerekir.",
        "e_no_bwrap": "bubblewrap kurulu değil (sudo pacman -S bubblewrap).",
        "e_too_big": "Bu dosya kutunun belleğine kopyalanamayacak kadar büyük.",
        "e_unreadable": "Dosya okunamadı.",
        "e_unusable_app": "Bu uygulama kutunun içinde çalışamaz.",
        "n_opened": "{file}, Glovebox içinde {app} ile açıldı — ağ yok, dosyalarınıza "
                    "erişim yok.",
        "n_failed": "{app} dosyayı Glovebox içinde açamadı: {detail}",
        "n_no_handler": "{file} dosyasını Glovebox içinde açabilecek bir uygulama yok. "
                        "Glovebox penceresinden bir uygulama seçin.",
        "n_error": "Glovebox: {detail}",
    },
}

_lang = "en"


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in STRINGS else "en"


def language() -> str:
    return _lang


def t(key: str, /, **kw) -> str:
    text = STRINGS.get(_lang, {}).get(key) or STRINGS["en"].get(key) or key
    return text.format(**kw) if kw else text

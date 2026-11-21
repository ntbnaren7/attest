"""Spoken help for the dashboard's main actions, in English, Hindi and Tamil.

Only these fixed keys can be spoken through /api/help/{key}; the browser cannot send free text to
text-to-speech. Audio is cached after the first request, so repeated help costs nothing.
"""
from __future__ import annotations

HELP: dict[str, dict[str, str]] = {
    "speak": {
        "en-IN": "Press and hold the black button, then speak. For example: what is the balance for L 1001? Let go when you finish. "
                 "I will check the payment records and answer. I can explain, but only you can approve money on the screen.",
        "hi-IN": "काले बटन को दबाकर रखें और बोलें। जैसे: L 1001 का बकाया कितना है? बोलना खत्म होने पर बटन छोड़ दें। "
                 "मैं भुगतान के रिकॉर्ड जाँचकर जवाब दूँगा। मैं समझा सकता हूँ, पर पैसे की मंज़ूरी सिर्फ़ आप स्क्रीन पर देते हैं।",
        "ta-IN": "கருப்பு பொத்தானை அழுத்திப் பிடித்துப் பேசுங்கள். உதாரணமாக: L 1001 கணக்கு நிலுவை என்ன? பேசி முடித்ததும் விடுங்கள். "
                 "நான் கட்டணப் பதிவுகளைச் சரிபார்த்துப் பதில் சொல்வேன். நான் விளக்குவேன், ஆனால் பணத்துக்கு அனுமதி நீங்கள் மட்டுமே திரையில் தர முடியும்.",
    },
    "send_link": {
        "en-IN": "This prepares a payment link for the exact amount still due. Nothing is sent until you check the details and approve.",
        "hi-IN": "इससे ठीक बाकी राशि का भुगतान लिंक तैयार होता है। जब तक आप जानकारी जाँचकर मंज़ूरी न दें, कुछ नहीं भेजा जाता।",
        "ta-IN": "இது நிலுவைத் தொகைக்குச் சரியான கட்டண இணைப்பைத் தயார் செய்கிறது. நீங்கள் விவரங்களைச் சரிபார்த்து அனுமதிக்கும் வரை எதுவும் அனுப்பப்படாது.",
    },
    "approve": {
        "en-IN": "Check the person, the phone number and the amount. Tick the box, then press approve. The link is made only with these exact details. "
                 "If anything changed in the meantime, for example a payment arrived, nothing is sent.",
        "hi-IN": "व्यक्ति, फ़ोन नंबर और राशि जाँचें। बॉक्स पर टिक करें, फिर मंज़ूर करें दबाएँ। लिंक सिर्फ़ इन्हीं जानकारियों से बनता है। "
                 "अगर बीच में कुछ बदला, जैसे भुगतान आ गया, तो कुछ नहीं भेजा जाएगा।",
        "ta-IN": "நபர், தொலைபேசி எண், தொகையைச் சரிபாருங்கள். பெட்டியில் டிக் செய்து அனுமதி பொத்தானை அழுத்துங்கள். இணைப்பு இந்த விவரங்களுடன் மட்டுமே உருவாகும். "
                 "இடையில் ஏதாவது மாறினால், உதாரணமாக பணம் வந்துவிட்டால், எதுவும் அனுப்பப்படாது.",
    },
    "check_again": {
        "en-IN": "This asks Cashfree again whether this person has paid. Use it if they say they paid just now.",
        "hi-IN": "इससे Cashfree से दोबारा पूछा जाता है कि इस व्यक्ति ने भुगतान किया या नहीं। अगर वे कहें कि अभी भुगतान किया है, तो इसे दबाएँ।",
        "ta-IN": "இந்த நபர் பணம் செலுத்தினாரா என்று Cashfree-இடம் மீண்டும் கேட்கும். இப்போதுதான் செலுத்தினேன் என்று சொன்னால் இதைப் பயன்படுத்துங்கள்.",
    },
    "add_list": {
        "en-IN": "Add a new list of people and amounts. Rows with mistakes are shown to you, not guessed. Details like religion, caste or area are never used.",
        "hi-IN": "लोगों और राशियों की नई सूची जोड़ें। गलत पंक्तियाँ आपको दिखाई जाती हैं, अंदाज़ा नहीं लगाया जाता। धर्म, जाति या इलाके जैसी जानकारी कभी इस्तेमाल नहीं होती।",
        "ta-IN": "நபர்கள், தொகைகள் கொண்ட புதிய பட்டியலைச் சேருங்கள். தவறான வரிகள் உங்களுக்குக் காட்டப்படும், யூகிக்கப்படாது. மதம், சாதி, பகுதி போன்ற விவரங்கள் ஒருபோதும் பயன்படுத்தப்படாது.",
    },
    "hear_why": {
        "en-IN": "Press the speaker to hear, in your language, why this person is in this group.",
        "hi-IN": "स्पीकर दबाएँ और अपनी भाषा में सुनें कि यह व्यक्ति इस समूह में क्यों है।",
        "ta-IN": "இந்த நபர் இந்தக் குழுவில் ஏன் இருக்கிறார் என்று உங்கள் மொழியில் கேட்க ஸ்பீக்கரை அழுத்துங்கள்.",
    },
    "group_CONTACT": {
        "en-IN": "Call now. These people owe money and nothing is unclear. The top of the list is most likely to pay if you ask today.",
        "hi-IN": "अभी कॉल करें। इन लोगों पर पैसा बाकी है और कुछ भी अस्पष्ट नहीं। सूची में सबसे ऊपर वाले आज पूछने पर सबसे ज़्यादा भुगतान करने की संभावना रखते हैं।",
        "ta-IN": "இப்போது அழைக்கவும். இவர்களுக்கு நிலுவை உள்ளது, எதுவும் குழப்பமில்லை. பட்டியலில் மேலே உள்ளவர்கள் இன்று கேட்டால் செலுத்த அதிக வாய்ப்புள்ளவர்கள்.",
    },
    "group_HOLD": {
        "en-IN": "Wait. These people promised to pay, asked not to be called, or already have a payment link. Do not call them now.",
        "hi-IN": "इंतज़ार करें। इन्होंने भुगतान का वादा किया है, कॉल न करने को कहा है, या इनके पास पहले से भुगतान लिंक है। अभी कॉल न करें।",
        "ta-IN": "காத்திருக்கவும். இவர்கள் செலுத்துவதாக வாக்களித்துள்ளனர், அழைக்க வேண்டாம் என்றனர், அல்லது ஏற்கனவே கட்டண இணைப்பு உள்ளது. இப்போது அழைக்க வேண்டாம்.",
    },
    "group_REVIEW": {
        "en-IN": "Check first. Something does not add up, for example they say they paid but we cannot find it, or they dispute the bill. A person should look before anyone calls.",
        "hi-IN": "पहले जाँचें। कुछ मेल नहीं खा रहा, जैसे वे कहते हैं भुगतान किया पर हमें नहीं मिला, या बिल पर विवाद है। किसी के कॉल करने से पहले एक व्यक्ति इसे देखे।",
        "ta-IN": "முதலில் சரிபார்க்கவும். ஏதோ பொருந்தவில்லை, உதாரணமாக செலுத்தினோம் என்கிறார்கள் ஆனால் கிடைக்கவில்லை, அல்லது பில்லை மறுக்கிறார்கள். யாரும் அழைக்கும் முன் ஒருவர் பார்க்க வேண்டும்.",
    },
    "group_CLOSED": {
        "en-IN": "Done. Cashfree confirmed the payment. We stop asking these people.",
        "hi-IN": "हो गया। Cashfree ने भुगतान की पुष्टि की है। अब इनसे नहीं माँगेंगे।",
        "ta-IN": "முடிந்தது. Cashfree கட்டணத்தை உறுதிப்படுத்தியது. இனி இவர்களிடம் கேட்க மாட்டோம்.",
    },
    "money": {
        "en-IN": "Still to collect is all money owed. Money received is only what Cashfree confirmed. On hold is money we should not ask for yet.",
        "hi-IN": "अभी वसूलना है यानी कुल बकाया। मिला हुआ पैसा वही है जिसकी Cashfree ने पुष्टि की। रुका हुआ वह पैसा है जो अभी नहीं माँगना चाहिए।",
        "ta-IN": "இன்னும் வசூலிக்க வேண்டியது என்பது மொத்த நிலுவை. வந்த பணம் என்பது Cashfree உறுதிப்படுத்தியது மட்டும். நிறுத்தி வைக்கப்பட்டது இப்போது கேட்கக்கூடாத பணம்.",
    },
}

LANGS = ("en-IN", "hi-IN", "ta-IN")

#!/bin/bash
# Fix the AIVoiceWatch product type after xcodegen generate
PBXPROJ="AIVoice/AIVoice.xcodeproj/project.pbxproj"
if [ ! -f "$PBXPROJ" ]; then
  PBXPROJ="AIVoice.xcodeproj/project.pbxproj"
fi

# Find the AIVoiceWatch native target block and fix its product type
python3 -c "
import re, sys
with open('$PBXPROJ', 'r') as f:
    content = f.read()

# Match the AIVoiceWatch target block and replace its productType
pattern = r'(/\* AIVoiceWatch \*/ = \{[^}]*productType = )\"com\.apple\.product-type\.application\"'
replacement = r'\1\"com.apple.product-type.application.watchapp2\"'
new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

if new_content != content:
    with open('$PBXPROJ', 'w') as f:
        f.write(new_content)
    print('Fixed AIVoiceWatch productType to application.watchapp2')
else:
    print('No change needed or pattern not found')
"

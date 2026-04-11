from modelscope import snapshot_download


print("Start downloading CosyVoice-300M-SFT model...")
snapshot_download(
    'iic/CosyVoice-300M-SFT', 
    local_dir='CosyVoice/pretrained_models/CosyVoice-300M-SFT')
print("Download completed!")
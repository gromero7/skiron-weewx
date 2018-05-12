# skiron-weewx
Skiron is a meteorological website and social network formed by users that post its observations in it. 

This is an extension in order to upload weather station data to skiron website.

### How to install

1. Use wee_extension utility:

```
wee_extension --install skiron-x-x-x.tgz
```

Inside weewx.conf you will see now the next lines:

```
[[Skiron]]
  cloud_key = replace_me
  enabled = false
  cloud_id = replace_me
```

2. Restart weewx service:
```
sudo service weewx restart
```


Enjoy!

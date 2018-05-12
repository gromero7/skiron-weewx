from setup import ExtensionInstaller


def loader():
    return SkironInstaller()


class SkironInstaller(ExtensionInstaller):
    def __init__(self):
        super(SkironInstaller, self).__init__(
            name='skr',
            description='Skiron weather station data uploader',
            version='0.3',
            author='Gerard Romero',
            author_email="gerard.romero7@gmail.com",
            restful_services='user.skr.Skiron',
            config={
                'StdRESTful': {
                    'Skiron': {
                        'enabled':   'false',
                        'cloud_id':  'replace_me',
                        'cloud_key': 'replace_me'
                    }
                }
            },
            files=[
                (
                    'bin/user', [
                        'bin/user/skr.py'
                    ]
                )
            ]

        )

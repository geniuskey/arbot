import { defineConfig } from 'vitepress'

export default defineConfig({
  base: '/arbot/',
  title: 'ArBot',
  description: '암호화폐 크로스 거래소 차익거래 자동화 시스템',
  lang: 'ko-KR',
  cleanUrls: true,

  head: [
    ['link', { rel: 'icon', href: '/arbot/logo.svg' }],
  ],

  themeConfig: {
    logo: '/logo.svg',
    siteTitle: 'ArBot',

    nav: [
      { text: '가이드', link: '/guide/introduction' },
      { text: '개념', link: '/concepts/architecture' },
      { text: '사용법', link: '/usage/paper-trading' },
      { text: '배포', link: '/deployment/docker' },
      { text: '개발', link: '/development/project-structure' },
    ],

    sidebar: {
      '/guide/': [
        {
          text: '시작하기',
          items: [
            { text: '프로젝트 소개', link: '/guide/introduction' },
            { text: '설치 가이드', link: '/guide/installation' },
            { text: '빠른 시작', link: '/guide/quick-start' },
            { text: '설정', link: '/guide/configuration' },
          ],
        },
      ],
      '/concepts/': [
        {
          text: '핵심 개념',
          items: [
            { text: '시스템 아키텍처', link: '/concepts/architecture' },
            { text: '차익거래 전략', link: '/concepts/strategies' },
            { text: '지원 거래소', link: '/concepts/exchanges' },
            { text: '용어 사전', link: '/concepts/glossary' },
          ],
        },
      ],
      '/usage/': [
        {
          text: '사용법',
          items: [
            { text: '페이퍼 트레이딩', link: '/usage/paper-trading' },
            { text: '백테스팅', link: '/usage/backtesting' },
            { text: '리스크 관리', link: '/usage/risk-management' },
            { text: '모니터링', link: '/usage/monitoring' },
            { text: '알림 설정', link: '/usage/alerts' },
          ],
        },
      ],
      '/deployment/': [
        {
          text: '배포',
          items: [
            { text: 'Docker 배포', link: '/deployment/docker' },
            { text: '환경 변수', link: '/deployment/environment' },
            { text: '프로덕션 배포', link: '/deployment/production' },
          ],
        },
      ],
      '/development/': [
        {
          text: '개발',
          items: [
            { text: '프로젝트 구조', link: '/development/project-structure' },
            { text: '테스트 가이드', link: '/development/testing' },
            { text: '개발 로드맵', link: '/development/roadmap' },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/geniuskey/arbot' },
    ],

    search: {
      provider: 'local',
      options: {
        translations: {
          button: {
            buttonText: '검색',
            buttonAriaLabel: '검색',
          },
          modal: {
            noResultsText: '검색 결과가 없습니다',
            resetButtonTitle: '초기화',
            footer: {
              selectText: '선택',
              navigateText: '이동',
              closeText: '닫기',
            },
          },
        },
      },
    },

    outline: {
      label: '목차',
    },

    docFooter: {
      prev: '이전',
      next: '다음',
    },

    lastUpdated: {
      text: '마지막 수정',
    },

    returnToTopLabel: '맨 위로',
    sidebarMenuLabel: '메뉴',
    darkModeSwitchLabel: '다크 모드',
  },
})
